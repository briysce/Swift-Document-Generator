# Meedo messaging runtime (fused)

This directory is Meedo-Me’s **messaging / WhatsApp gateway** runtime. It is
not a second product to install globally.

- Install: `python -m tools.meedo_me.runtime ensure`
- Run: `python -m tools.meedo_me.runtime gateway …`  
  (`openclaw` remains a technical alias that forwards to the upstream CLI)
- State: Meedo data dir (`OPENCLAW_HOME` / XDG `Meedo-Me/openclaw`)

Upstream Node packages are pinned in `package.json` for license and
reproducibility. See `tools/meedo_me/THIRD_PARTY.md`. Do **not**
`npm install -g openclaw`.

## WhatsApp Linked Devices (required once)

```bash
python -m tools.meedo_me.runtime ensure
python -m tools.meedo_me.runtime gateway channels login --channel whatsapp
# Scan the QR in WhatsApp → Linked devices within ~15s of each refresh.
python -m tools.meedo_me.runtime gateway channels status
```

### Cloud / datacenter hosts will not work

WhatsApp rejects Linked Devices pairing from many cloud egress IPs (AWS EC2,
GCP, Azure, Cursor Cloud Agents, etc.). Symptom on the phone:

> Couldn't Link device, try again later

That is **not** a Meedo config bug and is **not** fixed by refreshing the QR
on the same cloud host. Run the commands above on a normal PC (home/office
network), complete the scan there, then reuse the session under
`OPENCLAW_HOME` (`…/Meedo-Me/openclaw`). Do not commit QR PNGs or gateway tokens.
