# Headless Remote Runbook

## Purpose

Use an iPad to access the M4 Mac mini when no physical display is available, then optionally switch to Sidecar.

## Current local connection targets

- Bonjour host: `shuM4-Mac-min.local`
- Current LAN IP changes by network. Confirm with:
  - `ipconfig getifaddr en0 || ipconfig getifaddr en1 || hostname`
- Quick readiness check:
  - `python3 /Users/shu/OpenClaw/scripts/roby-headless-remote-check.py`

## Preconditions

- Mac mini: Screen Sharing is ON
- Access: Only user `shu`
- iPad and Mac mini are on the same Wi-Fi/LAN
- RealVNC Viewer: Remote Desktop is installed on iPad

## iPad connection steps

1. Open RealVNC Viewer on iPad.
2. Try connecting to `shuM4-Mac-min.local`.
3. If discovery/name resolution fails, use the current LAN IP address.
4. Authenticate with the Mac login credentials.
5. Once the Mac desktop is visible, operate normally.

## Sidecar handoff

1. While connected over VNC, open Control Center on the Mac.
2. Open Display / Screen Mirroring.
3. Select the iPad.
4. The iPad will switch from VNC client to Sidecar display.

## New network checklist

When moving to a different house/network:

1. Join the Mac mini to the local Wi-Fi.
2. Run `python3 /Users/shu/OpenClaw/scripts/roby-headless-remote-check.py`.
3. First try `shuM4-Mac-min.local` from iPad.
4. If that fails, connect directly to the LAN IP shown in the check output.

## Notes

- The LAN IP will usually change between home and parents' house.
- `.local` is preferred when it resolves correctly.
- This setup is local-only; nothing is intentionally exposed to the internet.
