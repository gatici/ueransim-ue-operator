<div align="center">
  <img src="./icon.png" alt="UERANSIM Icon" width="200" height="200">
</div>
<br/>
<div align="center">
  <a href="https://charmhub.io/ueransim-ue-operator"><img src="https://charmhub.io/ueransim-ue-operator/badge.svg" alt="CharmHub Badge"></a>
  <a href="https://github.com/canonical/ueransim-ue-operator/actions/workflows/publish-charm.yaml">
    <img src="https://github.com/canonical/ueransim-ue-operator/actions/workflows/publish-charm.yaml/badge.svg?branch=main" alt=".github/workflows/publish-charm.yaml">
  </a>
  <br/>
  <br/>
  <h1>UERANSIM UE Operator</h1>
</div>

A Charmed Operator for Ali Gungor's RAN simulator (ueransim) UE component.

## Usage

```bash
juju deploy ueransim-ue-operator --trust --channel=edge
juju run ueransim-ue-operator/leader start-ue
```

## Image

- **ueransim**: `ghcr.io/canonical/ueransim:3.2.6`
