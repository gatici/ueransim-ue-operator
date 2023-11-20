#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed operator for the 5G uesim service."""

import logging
from typing import Optional
from jinja2 import Environment, FileSystemLoader
from lightkube.core.client import Client
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Service
from ops.charm import CharmBase, InstallEvent, RemoveEvent
from ops.framework import EventBase, StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import Layer

logger = logging.getLogger(__name__)

BASE_CONFIG_PATH = "/etc"
UE_CONFIG_FILE_NAME = "ue.yaml"
DEFAULT_FIELD_MANAGER = "controller"
GTP_PORT = 4997


class UESIMOperatorCharm(CharmBase):
    """Main class to describe juju event handling for the UE RAN simulator operator."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self._stored.set_default(ue_running=False)

        self._uesim_container_name = self._uesim_service_name = "uesim"
        self._uesim_container = self.unit.get_container(self._uesim_container_name)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.remove, self._on_remove)
        self.framework.observe(self.on.config_changed, self._configure)
        self.framework.observe(self.on.uesim_pebble_ready, self._configure)
        self.framework.observe(self.on.start_ue_action, self._on_start_ue_action)
        self.framework.observe(self.on.stop_ue_action, self._on_stop_ue_action)

    def _configure(self, event: EventBase) -> None:
        """Juju event handler.

        Sets unit status, writes uesim configuration file and sets ip route.

        Args:
            event: Juju event
        """
        if invalid_configs := self._get_invalid_configs():
            self.unit.status = BlockedStatus(f"Configurations are invalid: {invalid_configs}")
            return
        if not self._uesim_container.can_connect():
            self.unit.status = WaitingStatus("Waiting for container to be ready")
            return
        if not self._uesim_container.exists(path=BASE_CONFIG_PATH):
            self.unit.status = WaitingStatus("Waiting for storage to be attached")
            return

        content = self._render_ue_config_file(
            mcc=self._get_mcc_from_config(),  # type: ignore[arg-type]
            mnc=self._get_mnc_from_config(),  # type: ignore[arg-type]
            sd=self._get_sd_from_config(),  # type: ignore[arg-type]
            sst=self._get_sst_from_config(),  # type: ignore[arg-type]
            supi=self._get_supi_from_config(),  # type: ignore[arg-type]
            usim_key=self._get_usim_key_from_config(),  # type: ignore[arg-type]
            usim_opc=self._get_usim_opc_from_config(),  # type: ignore[arg-type]
            imei=self._get_imei_from_config(),  # type: ignore[arg-type]
            gnb_address=self._get_gnb_address_from_config(),  # type: ignore[arg-type]
        )
        if not self._config_file_content_matches(content=content):
            self._write_config_file(content=content)
            self._configure_uesim_workload(restart=True)
        self.unit.status = ActiveStatus()

    def _on_install(self, event: InstallEvent) -> None:
        client = Client()
        client.apply(
            Service(
                apiVersion="v1",
                kind="Service",
                metadata=ObjectMeta(
                    namespace=self.model.name,
                    name=f"{self.app.name}",
                    labels={
                        "app.kubernetes.io/name": self.app.name,
                    },
                ),
                spec=ServiceSpec(
                    selector={"app.kubernetes.io/name": self.app.name},
                    ports=[
                        ServicePort(name="ue-gtp", port=GTP_PORT, protocol="UDP"),
                    ],
                ),
            ),
            field_manager=DEFAULT_FIELD_MANAGER,
        )
        logger.info("Created/asserted existence of UE service")

    def _on_remove(self, event: RemoveEvent) -> None:
        client = Client()
        client.delete(
            Service,
            namespace=self.model.name,
            name=f"{self.app.name}",
        )
        logger.info("Removed external gNB service")

    def _on_start_ue_action(self, event: EventBase) -> None:
        logger.info("Starting UE service")
        self._configure(event)
        if not self._stored.ue_running:
            self._uesim_container.start(self._uesim_service_name)
            logger.info("Started UE service")
            self._stored.ue_running = True

    def _on_stop_ue_action(self, event: EventBase) -> None:
        logger.info("Stopping UE service")
        self._configure(event)
        if self._stored.ue_running:
            self._uesim_container.stop(self._uesim_service_name)
            logger.info("Stopped UE service")
            self._stored.ue_running = False

    def _configure_uesim_workload(self, restart: bool = False) -> None:
        """Configures pebble layer for the gNB simulator container.

        Args:
            restart (bool): Whether to restart the uesim container.
        """
        plan = self._uesim_container.get_plan()
        layer = self._uesim_pebble_layer
        if plan.services != layer.services or restart:
            self._uesim_container.add_layer("uesim", layer, combine=True)
            if self._stored.ue_running:
                self._uesim_container.restart(self._uesim_service_name)

    def _get_gnb_address_from_config(self) -> Optional[str]:
        return self.model.config.get("gnb-address")

    def _get_usim_opc_from_config(self) -> Optional[str]:
        return self.model.config.get("usim-opc")

    def _get_mcc_from_config(self) -> Optional[str]:
        return self.model.config.get("mcc")

    def _get_imei_from_config(self) -> Optional[str]:
        return self.model.config.get("imei")

    def _get_mnc_from_config(self) -> Optional[str]:
        return self.model.config.get("mnc")

    def _get_usim_key_from_config(self) -> Optional[str]:
        return self.model.config.get("usim-key")

    def _get_sd_from_config(self) -> Optional[str]:
        return self.model.config.get("sd")

    def _get_sst_from_config(self) -> Optional[int]:
        return self.model.config.get("sst")  # type: ignore[arg-type]

    def _get_supi_from_config(self) -> Optional[str]:
        return self.model.config.get("supi")

    def _write_config_file(self, content: str) -> None:
        self._uesim_container.push(source=content, path=f"{BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}")
        logger.info(f"Config file written {BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}")

    def _ue_config_file_is_written(self) -> bool:
        if not self._uesim_container.exists(f"{BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}"):
            return False
        return True

    def _render_ue_config_file(
        self,
        *,
        mcc: str,
        mnc: str,
        sd: str,
        sst: str,
        supi: str,
        usim_key: str,
        usim_opc: str,
        imei: str,
        gnb_address: str,
    ) -> str:
        """Renders config file based on parameters.

        Args:
            mcc: Mobile Country Code
            mnc: Mobile Network Code
            sd: Slice ID
            sst: Slice Selection Type
            supi: IMSI number of the UE
            usim_key: USIM Key
            usim_opc: USIM Operator Key
            imei: IMEI number of the device. It is used if no SUPI is provided
            gnb_address: gNB's IP address on the RAN network
        Returns:
            str: Rendered ue configuration file
        """
        jinja2_env = Environment(loader=FileSystemLoader("src/templates"))
        template = jinja2_env.get_template("ue-config.yaml.j2")
        return template.render(
            mcc=mcc,
            mnc=mnc,
            sd=sd,
            sst=sst,
            supi=supi,
            usim_key=usim_key,
            usim_opc=usim_opc,
            imei=imei,
            gnb_address=gnb_address,
        )

    def _config_file_content_matches(self, content: str) -> bool:
        """Returns whether the gnb config file content matches the provided content.

        Returns:
            bool: Whether the gnb config file content matches
        """
        if not self._uesim_container.exists(path=f"{BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}"):
            return False
        existing_content = self._uesim_container.pull(path=f"{BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}")
        if existing_content.read() != content:
            return False
        return True

    def _get_invalid_configs(self) -> list[str]:  # noqa: C901
        """Gets list of invalid Juju configurations."""
        invalid_configs = []
        if not self._get_gnb_address_from_config():
            invalid_configs.append("gnb-address")
        if not self._get_supi_from_config():
            invalid_configs.append("supi")
        if not self._get_mcc_from_config():
            invalid_configs.append("mcc")
        if not self._get_mnc_from_config():
            invalid_configs.append("mnc")
        if not self._get_usim_key_from_config():
            invalid_configs.append("usim-key")
        if not self._get_sd_from_config():
            invalid_configs.append("sd")
        if not self._get_sst_from_config():
            invalid_configs.append("sst")
        if not self._get_usim_opc_from_config():
            invalid_configs.append("usim-opc")
        if not self._get_imei_from_config():
            invalid_configs.append("imei")
        return invalid_configs

    @property
    def _uesim_pebble_layer(self) -> Layer:
        return Layer(
            {
                "summary": "uesim simulator layer",
                "description": "pebble config layer for gnb simulator",
                "services": {
                    self._uesim_service_name: {
                        "override": "replace",
                        "startup": "enabled",
                        "command": f"nr-ue -c {BASE_CONFIG_PATH}/{UE_CONFIG_FILE_NAME}",  # noqa: E501
                    },
                },
            }
        )


if __name__ == "__main__":  # pragma: nocover
    main(UESIMOperatorCharm)
