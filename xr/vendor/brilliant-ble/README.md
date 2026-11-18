# brilliant-ble 1.2.0

The Bluetooth library Brilliant Labs publishes for its glasses (Halo and Frame), for use in a browser.

- From: the npm package `brilliant-ble`, version 1.2.0, file `dist/brilliant-ble.es.js`. Not changed.
- Source: https://github.com/brilliantlabsAR/brilliant_sdk (folder `webbluetooth/packages/brilliant-ble`).
- Licence: BSD-3-Clause, in `LICENSE` beside this file.
- sha256 of `brilliant-ble.es.js`: `3c9eed5e157f066d02766f754867ba3ce2e06c4a41819e9be10608c50344a69b`

It is here so the Studio can connect to a pair of Halo glasses without anything being installed. The framework in
`src/` does not import it: `haloHost` is handed the connected object. Only `studio/glasses.js` loads this file,
and only when someone presses the button to connect.

To update it: `npm pack brilliant-ble`, copy `dist/brilliant-ble.es.js` and `LICENSE` here, and change the
version and the sha256 above.
