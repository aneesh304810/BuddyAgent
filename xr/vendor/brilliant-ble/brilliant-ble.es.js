var T = Object.defineProperty;
var E = (c, t, i) => t in c ? T(c, t, { enumerable: !0, configurable: !0, writable: !0, value: i }) : c[t] = i;
var r = (c, t, i) => E(c, typeof t != "symbol" ? t + "" : t, i);
function A(c) {
  const t = Math.floor((c + 7) / 251);
  return t === 0 ? c : t * 251 - 7;
}
var D = /* @__PURE__ */ ((c) => (c.FRAME = "Frame", c.HALO = "Halo", c.UNKNOWN = "Unknown", c))(D || {});
class C {
  /**
   * Creates an instance of BrilliantBle.
   * The constructor is currently empty as most setup occurs during the connect method.
   */
  constructor() {
    r(this, "device");
    r(this, "server");
    r(this, "txCharacteristic");
    r(this, "rxCharacteristic");
    r(this, "audioTxCharacteristic");
    r(this, "deviceType", "Unknown");
    r(this, "SERVICE_UUID", "7a230001-5475-a6a4-654c-8431f6ad49c4");
    r(this, "TX_CHARACTERISTIC_UUID", "7a230002-5475-a6a4-654c-8431f6ad49c4");
    r(this, "RX_CHARACTERISTIC_UUID", "7a230003-5475-a6a4-654c-8431f6ad49c4");
    r(this, "AUDIO_TX_CHARACTERISTIC_UUID", "7a230005-5475-a6a4-654c-8431f6ad49c4");
    r(this, "maxPayload", 60);
    // will be set after connection
    r(this, "awaitingPrintResponse", !1);
    r(this, "awaitingDataResponse", !1);
    r(this, "printTimeoutId");
    r(this, "printResponsePromise");
    r(this, "printResolve");
    // Updated promise and resolve types for data responses
    r(this, "dataResponsePromise");
    r(this, "dataResolve");
    r(this, "dataReject");
    // Updated handler types
    r(this, "onDataResponse");
    r(this, "onPrintResponse");
    r(this, "onDisconnectHandler");
    // sendMessage() flow control on Halo: packets sent but not yet acknowledged,
    // and the waiters to wake on each ACK (true) or on disconnect (false)
    r(this, "acksOutstanding", 0);
    r(this, "ackFailed", !1);
    r(this, "ackWaiters", /* @__PURE__ */ new Set());
    /**
     * Size in bytes of the device's data receive queue. On Halo, sendMessage()
     * keeps no more packets unacknowledged than fit in it, which is what keeps
     * a slow device-side handler from losing data. Set it to match a device
     * app that calls frame.bluetooth.receive_buffer_size(); larger values
     * allow faster sends. Defaults to Halo's 4096 bytes.
     */
    r(this, "receiveBufferSize", 4096);
    r(this, "handleDisconnect", () => {
      this.device = void 0, this.server = void 0, this.txCharacteristic = void 0, this.rxCharacteristic = void 0, this.audioTxCharacteristic = void 0, this.deviceType = "Unknown", this.acksOutstanding = 0, this.ackFailed = !1, this.wakeAckWaiters(!1), this.awaitingDataResponse && this.dataReject && (this.awaitingDataResponse = !1, this.dataResolve = void 0, this.dataReject(new Error("Disconnected while awaiting a data response"))), this.dataReject = void 0, this.onDisconnectHandler && this.onDisconnectHandler();
    });
    r(this, "notificationHandler", (t) => {
      const e = t.target.value;
      if (!(!e || e.buffer.byteLength === 0))
        if (e.byteLength > 0 && e.getUint8(0) === 1) {
          const s = new Uint8Array(e.buffer, e.byteOffset + 1, e.byteLength - 1);
          if (this.acksOutstanding > 0 && e.byteLength === 4 && e.getUint8(1) === 1 && e.getUint8(2) === 0 ? (this.acksOutstanding--, e.getUint8(3) !== 0 && (this.ackFailed = !0), this.wakeAckWaiters(!0)) : this.awaitingDataResponse && this.dataResolve && (this.awaitingDataResponse = !1, this.dataResolve(s)), this.onDataResponse) {
            const a = this.onDataResponse(s);
            a instanceof Promise && a.catch(console.error);
          }
        } else {
          const s = new TextDecoder().decode(e);
          if (this.awaitingPrintResponse && this.printResolve && (this.awaitingPrintResponse = !1, this.printResolve(s)), this.onPrintResponse) {
            const a = this.onPrintResponse(s);
            a instanceof Promise && a.catch(console.error);
          }
        }
    });
  }
  /**
   * Returns the type of the connected Brilliant device.
   */
  get type() {
    return this.deviceType;
  }
  /**
   * Sets or updates the handler for asynchronous data responses from the device.
   * @param handler The function to call when data (as Uint8Array) is received.
   * Pass undefined to remove the current handler.
   */
  setDataResponseHandler(t) {
    this.onDataResponse = t;
  }
  /**
   * Sets or updates the handler for asynchronous print (string) responses from the device.
   * @param handler The function to call when a print string is received.
   * Pass undefined to remove the current handler.
   */
  setPrintResponseHandler(t) {
    this.onPrintResponse = t;
  }
  /**
   * Sets or updates the handler for disconnection events.
   * @param handler The function to call when the device disconnects.
   * Pass undefined to remove the current handler.
   */
  setDisconnectHandler(t) {
    this.onDisconnectHandler = t;
  }
  /**
   * Attempts to establish a connection with the device and set up characteristics.
   * This method is intended to be called internally by `connect` and handles a single connection attempt.
   */
  async _attemptConnection() {
    var t, i, e, s;
    if (!this.device || !this.device.gatt)
      throw new Error("Bluetooth device or GATT interface not available for connection attempt.");
    this.txCharacteristic = void 0, this.rxCharacteristic = void 0, this.audioTxCharacteristic = void 0, this.deviceType = "Unknown", this.server = void 0;
    try {
      console.log(`Attempting to connect to GATT server on device: ${this.device.name || this.device.id}...`), this.server = await this.device.gatt.connect(), console.log("GATT server connected."), console.log("Getting primary service...");
      const a = await this.server.getPrimaryService(this.SERVICE_UUID);
      console.log("Primary service obtained."), console.log("Getting TX characteristic..."), this.txCharacteristic = await a.getCharacteristic(this.TX_CHARACTERISTIC_UUID), console.log("TX characteristic obtained."), console.log("Getting RX characteristic..."), this.rxCharacteristic = await a.getCharacteristic(this.RX_CHARACTERISTIC_UUID), console.log("RX characteristic obtained.");
      try {
        this.audioTxCharacteristic = await a.getCharacteristic(this.AUDIO_TX_CHARACTERISTIC_UUID), this.deviceType = "Halo", console.log("Audio TX characteristic obtained — device is Halo.");
      } catch {
        this.deviceType = "Frame", console.log("No audio TX characteristic — device is Frame.");
      }
      console.log("Starting notifications on RX characteristic..."), await this.rxCharacteristic.startNotifications(), this.rxCharacteristic.addEventListener("characteristicvaluechanged", this.notificationHandler), console.log("Notifications started."), await this.sendBreakSignal(!1), console.log("Fetching MTU size (max_length) from device...");
      const n = await this.sendLua("print(frame.bluetooth.max_length())", { awaitPrint: !0 });
      if (n == null)
        throw new Error("Failed to get MTU size from device: no response.");
      const o = parseInt(n);
      if (isNaN(o) || o <= 0)
        throw new Error(`Invalid MTU size received: '${n}'`);
      this.maxPayload = o, console.log(`MTU size set to: ${this.maxPayload}`);
    } catch (a) {
      if (console.error("Error during connection attempt:", a), this.rxCharacteristic) {
        try {
          (i = (t = this.device) == null ? void 0 : t.gatt) != null && i.connected && await this.rxCharacteristic.stopNotifications();
        } catch {
        }
        this.rxCharacteristic.removeEventListener("characteristicvaluechanged", this.notificationHandler), this.rxCharacteristic = void 0;
      }
      throw this.txCharacteristic = void 0, this.audioTxCharacteristic = void 0, this.deviceType = "Unknown", (s = (e = this.device) == null ? void 0 : e.gatt) != null && s.connected && this.device.gatt.disconnect(), this.server = void 0, a;
    }
  }
  /**
   * Connects to a Frame device.
   *
   * Prompts the user to select a Bluetooth device if one is not already known or connected.
   * Handles connection attempts, retries, and characteristic setup.
   *
   * @param options - Optional configuration for the connection process.
   * @param options.name - The exact name of the device to connect to.
   * @param options.namePrefix - The prefix of the device name to filter by.
   * @param options.numAttempts - The maximum number of connection attempts. Defaults to 5.
   * @param options.retryDelayMs - The delay in milliseconds between retry attempts. Defaults to 1000ms.
   * @returns A promise that resolves with the name or ID of the connected device, or undefined if connection fails.
   * @throws Error if Web Bluetooth is not available, if device selection is cancelled, or if connection fails after all attempts.
   */
  async connect(t = {}) {
    var d;
    const { name: i, namePrefix: e, numAttempts: s = 5, retryDelayMs: a = 1e3 } = t;
    if (!navigator.bluetooth)
      throw new Error("Web Bluetooth API not available.");
    if (!this.device) {
      const l = {
        filters: [i ? { services: [this.SERVICE_UUID], name: i } : e ? { services: [this.SERVICE_UUID], namePrefix: e } : { services: [this.SERVICE_UUID] }],
        optionalServices: [this.SERVICE_UUID]
      };
      try {
        if (console.log("Requesting Bluetooth device from user..."), this.device = await navigator.bluetooth.requestDevice(l), !this.device)
          throw new Error("No device selected by the user.");
        console.log(`Device selected: ${this.device.name || this.device.id}`);
      } catch (w) {
        throw console.error("Bluetooth device request failed:", w), this.device = void 0, w;
      }
    }
    if (!this.device)
      throw new Error("Device not available after selection phase.");
    const n = this.device;
    n.removeEventListener("gattserverdisconnected", this.handleDisconnect), n.addEventListener("gattserverdisconnected", this.handleDisconnect);
    let o;
    for (let h = 1; h <= s; h++) {
      if (!this.device) {
        console.warn(`Device (id: ${n.id}) was disconnected externally during connection attempts.`), o = o || new Error(`Device disconnected externally during connection attempt ${h}.`);
        break;
      }
      try {
        return console.log(`Connection attempt ${h} of ${s} to device '${n.name || n.id}'...`), await this._attemptConnection(), console.log(`Successfully connected to ${n.name || n.id} on attempt ${h}.`), n.name || n.id || "Unknown Device";
      } catch (l) {
        if (o = l, console.error(`Attempt ${h} to connect to '${n.name || n.id}' failed:`, l), l instanceof Error && l.name === "NetworkError" && // DOMException name
        (l.message.includes("Connection attempt failed.") || l.message.includes("GATT operation failed for unknown reason.") || l.message.includes("GATT Server is disconnected.") || // Potentially retryable if transient
        l.message.includes("Bluetooth device is already connected.")) && h < s)
          console.log(`Retryable error encountered. Retrying in ${a / 1e3}s...`), await new Promise((u) => setTimeout(u, a));
        else {
          console.log("Non-retryable error or max attempts reached. Aborting connection process.");
          break;
        }
      }
    }
    throw console.error(`Failed to connect to device '${n.name || n.id}' after ${s} attempts or due to external disconnection.`), n.removeEventListener("gattserverdisconnected", this.handleDisconnect), (d = n.gatt) != null && d.connected && n.gatt.disconnect(), this.server = void 0, this.txCharacteristic = void 0, this.rxCharacteristic = void 0, this.audioTxCharacteristic = void 0, this.deviceType = "Unknown", this.device = void 0, o || new Error(`Failed to connect to ${n.name || n.id} after ${s} attempts. No specific error recorded, or device disconnected externally.`);
  }
  /**
   * Disconnects from the currently connected Frame device.
   * If no device is connected, this method does nothing.
   * It also triggers the `handleDisconnect` cleanup logic if not connected,
   * or relies on the 'gattserverdisconnected' event if connected.
   * @returns A promise that resolves once the disconnection process has been initiated, or immediately if already disconnected.
   */
  async disconnect() {
    var i, e;
    const t = this.device;
    t && ((i = t.gatt) != null && i.connected) ? (await this.settleAcks(), (e = t.gatt) == null || e.disconnect()) : this.handleDisconnect();
  }
  /**
   * Checks if the Frame device is currently connected.
   * @returns True if the device is connected, false otherwise.
   */
  isConnected() {
    return !!(this.device && this.device.gatt && this.device.gatt.connected);
  }
  /**
   * Gets the maximum payload size for a single BLE transmission.
   * This value is determined after device connection.
   * For Lua strings, the full `maxPayload` can be used.
   * For data packets (which include a 0x01 prefix byte), the effective payload is `maxPayload - 1`.
   * @param isLua If true, returns the max payload for Lua strings. Otherwise, for data packets.
   * @returns The maximum payload size in bytes.
   */
  getMaxPayload(t) {
    return t ? this.maxPayload : this.maxPayload - 1;
  }
  async transmit(t, i = !1, e = !0) {
    if (!this.txCharacteristic)
      throw new Error("Not connected or TX characteristic not available.");
    if (t.byteLength > this.maxPayload)
      throw new Error(`Payload length: ${t.byteLength} exceeds maximum BLE packet size: ${this.maxPayload}`);
    i && console.log("Transmitting (hex):", Array.from(t).map((a) => a.toString(16).padStart(2, "0")).join(" "));
    const s = t;
    e ? await this.txCharacteristic.writeValueWithResponse(s) : await this.txCharacteristic.writeValueWithoutResponse(s);
  }
  wakeAckWaiters(t) {
    const i = [...this.ackWaiters];
    this.ackWaiters.clear();
    for (const e of i)
      e(t);
  }
  /**
   * Waits until at most maxOutstanding sendMessage() packets are
   * unacknowledged. Throws if the device reported a failed packet, stops
   * acknowledging for timeoutMs, or disconnects.
   */
  async awaitAcks(t, i = 5e3) {
    const e = () => {
      if (this.ackFailed)
        throw this.ackFailed = !1, this.acksOutstanding = 0, new Error("Device failed to process a message (see its print output).");
    };
    for (e(); this.acksOutstanding > t; ) {
      const s = await new Promise((a) => {
        const n = setTimeout(() => {
          this.ackWaiters.delete(o), a(void 0);
        }, i), o = (d) => {
          clearTimeout(n), a(d);
        };
        this.ackWaiters.add(o);
      });
      if (e(), s === void 0)
        throw this.acksOutstanding = 0, new Error(`Device didn't acknowledge message data within ${i}ms.`);
      if (!s || !this.isConnected())
        throw new Error("Disconnected while awaiting message acknowledgements.");
    }
    e();
  }
  /**
   * Waits until the device has acknowledged every packet sendMessage() has
   * sent, i.e. its data handler has taken them all in.
   *
   * On Halo, sendMessage() resolves once its last packet is written, so that
   * consecutive messages stream back to back. Every other call that talks to
   * the device waits for this first, so data is always processed before,
   * say, a break signal; call it directly to wait for that point.
   * Resolves immediately if nothing is outstanding (always the case on Frame).
   * @param timeoutMs Fail if no ACK arrives for this long. Defaults to 5000ms.
   * @throws Error if the device reported a failed packet, stopped acknowledging, or disconnected.
   */
  async waitForAcks(t = 5e3) {
    await this.awaitAcks(0, t);
  }
  // Before a control signal or disconnect: wait briefly, but a stuck device
  // app is exactly what break/reset/remove are for, so never fail on it
  async settleAcks() {
    try {
      await this.awaitAcks(0, 1e3);
    } catch {
      this.acksOutstanding = 0, this.ackFailed = !1;
    }
  }
  /**
   * Sends a Lua command string to the Frame device.
   * @param str The Lua command string to send.
   * @param options Optional configuration for sending the Lua command.
   * @param options.showMe If true, logs the transmitted data (hex format) to the console. Defaults to false.
   * @param options.awaitPrint If true, waits for a print response from the device. Defaults to false.
   * @param options.timeout The timeout in milliseconds to wait for a print response if `awaitPrint` is true. Defaults to 5000ms.
   * @returns A promise that resolves with the print response string if `awaitPrint` is true, or void otherwise.
   * @throws Error if the Lua string payload is too large or if a timeout occurs while awaiting a print response,
   * or if earlier sendMessage() data is not acknowledged (see `waitForAcks()`).
   */
  async sendLua(t, i = {}) {
    const { showMe: e = !1, awaitPrint: s = !1, timeout: a = 5e3 } = i, n = new TextEncoder().encode(t);
    if (n.byteLength > this.getMaxPayload(!0))
      throw new Error(`Lua string payload (${n.byteLength} bytes) is too large for max Lua payload (${this.getMaxPayload(!0)} bytes).`);
    if (await this.waitForAcks(), s && (this.printTimeoutId && clearTimeout(this.printTimeoutId), this.awaitingPrintResponse = !0, this.printResponsePromise = new Promise((o, d) => {
      this.printResolve = o, this.printTimeoutId = setTimeout(() => {
        this.awaitingPrintResponse && (this.awaitingPrintResponse = !1, this.printResolve = void 0, d(new Error(`Device didn't respond with a print within ${a}ms.`)));
      }, a);
    }).finally(() => {
      this.printTimeoutId && (clearTimeout(this.printTimeoutId), this.printTimeoutId = void 0);
    })), await this.transmit(n, e), s) return this.printResponsePromise;
  }
  /**
   * Sends raw data to the device. The data is prefixed with a `0x01` byte to distinguish it from Lua commands.
   * @param data The raw application payload to send as a Uint8Array. This is the actual data without the prefix.
   * @param options Optional configuration for sending data.
   * @param options.showMe If true, logs the transmitted data (including prefix, hex format) to the console. Defaults to false.
   * @param options.awaitData If true, waits for a data response from the device. Defaults to false.
   * @param options.timeout The timeout in milliseconds to wait for a data response if `awaitData` is true. Defaults to 5000ms.
   * @param options.awaitBtResponse If true, uses write-with-response at the BLE level. Defaults to true.
   * @returns A promise that resolves with the Uint8Array data response if `awaitData` is true, or void otherwise.
   * @throws Error if not connected, if TX characteristic is not available, or if the data payload is too large,
   * or if earlier sendMessage() data is not acknowledged (see `waitForAcks()`).
   */
  async sendData(t, i = {}) {
    const { showMe: e = !1, awaitData: s = !1, timeout: a = 5e3, awaitBtResponse: n = !0 } = i;
    if (!this.txCharacteristic)
      throw new Error("Not connected or TX characteristic not available.");
    if (t.byteLength > this.getMaxPayload(!1))
      throw new Error(`Data payload (${t.byteLength} bytes) is too large for max data payload (${this.getMaxPayload(!1)} bytes).`);
    await this.waitForAcks();
    const o = new Uint8Array([1]), d = new Uint8Array(o.length + t.byteLength);
    d.set(o, 0), d.set(t, o.length);
    let h;
    if (s && (this.awaitingDataResponse = !0, this.dataResponsePromise = new Promise((l, w) => {
      this.dataResolve = l, this.dataReject = w, h = setTimeout(() => {
        this.awaitingDataResponse && (this.awaitingDataResponse = !1, this.dataResolve = void 0, w(new Error(`Device didn't respond with data within ${a}ms.`)));
      }, a);
    }).finally(() => {
      h && clearTimeout(h);
    })), await this.transmit(d, e, n), s) return this.dataResponsePromise;
  }
  /**
   * Sends audio data to the Halo audio characteristic (UUID 7a230005).
   * Packets exceeding the MTU are silently dropped, matching device firmware behaviour.
   * @param data The audio payload (LC3 frames or PCM samples) to send.
   * @param awaitBtResponse If true, uses write-with-response at the BLE level. Defaults to false for throughput.
   */
  async sendAudio(t, i = !1) {
    if (!this.audioTxCharacteristic)
      throw new Error("Audio TX characteristic not available. Is this a Halo device?");
    const e = this.maxPayload;
    if (t.byteLength > e)
      return;
    const s = t;
    i ? await this.audioTxCharacteristic.writeValueWithResponse(s) : await this.audioTxCharacteristic.writeValueWithoutResponse(s);
  }
  /**
   * Discards any unsolicited print output currently arriving on the channel
   * (e.g. the 'interrupted'/reboot banner produced by a break or reset), so
   * the next `sendLua({ awaitPrint: true })` receives a clean response rather
   * than the stray banner.
   *
   * Bounded so it never hangs: returns after `quietMs` of silence, or
   * `maxTotalMs` at the latest. An idle device emits nothing, so the common
   * case costs a single quiet window. A break only prints 'interrupted' when
   * it actually interrupts a running chunk, and the banner can span more than
   * one notification, so we soak up everything until the channel goes quiet
   * rather than awaiting a single line.
   * @param quietMs Silence (ms) that marks the channel drained. Defaults to 250.
   * @param maxTotalMs Hard cap (ms) so it always returns. Defaults to 1500.
   */
  async drainPrintChannel(t = 250, i = 1500) {
    const e = Date.now();
    for (; Date.now() - e < i; ) {
      const s = await new Promise((a) => {
        this.printTimeoutId && clearTimeout(this.printTimeoutId), this.awaitingPrintResponse = !0, this.printResolve = () => a(!0), this.printTimeoutId = setTimeout(() => {
          this.awaitingPrintResponse && (this.awaitingPrintResponse = !1, this.printResolve = void 0, a(!1));
        }, t);
      });
      if (this.printTimeoutId && (clearTimeout(this.printTimeoutId), this.printTimeoutId = void 0), !s) break;
    }
    this.awaitingPrintResponse = !1, this.printResolve = void 0;
  }
  /**
   * Sends a reset signal (0x04) to the Frame device.
   * This typically causes the device to restart its Lua environment.
   * @param showMe If true, logs the transmitted signal (hex format) to the console. Defaults to false.
   * @returns A promise that resolves after a short delay post-transmission.
   */
  async sendResetSignal(t = !1) {
    const i = new Uint8Array([4]);
    await this.settleAcks(), await this.transmit(i, t), await new Promise((e) => setTimeout(e, 200));
  }
  /**
   * Sends a remove signal (0x05) to the Halo device, which removes main.lua.
   * This is only applicable to Halo devices.
   * @param showMe If true, logs the transmitted signal (hex format) to the console. Defaults to false.
   * @returns A promise that resolves after a short delay post-transmission.
   */
  async sendRemoveSignal(t = !1) {
    const i = new Uint8Array([5]);
    await this.settleAcks(), await this.transmit(i, t), await new Promise((e) => setTimeout(e, 200));
  }
  /**
   * Sends a break signal (0x03) to the Frame device.
   * This typically interrupts any currently running Lua script on the device.
   * @param showMe If true, logs the transmitted signal (hex format) to the console. Defaults to false.
   * @returns A promise that resolves after a short delay post-transmission.
   */
  async sendBreakSignal(t = !1) {
    const i = new Uint8Array([3]);
    await this.settleAcks(), await this.transmit(i, t), await new Promise((e) => setTimeout(e, 200));
  }
  /**
   * Uploads content to a file on the Frame device by sending Lua commands.
   * The content is escaped and chunked to fit within payload limits.
   * @param content The string content to write to the file.
   * @param frameFilePath The path to the file on the Frame device. Defaults to "main.lua".
   * @returns A promise that resolves when the file upload is complete.
   * @throws Error if any step of the file upload process fails (e.g., opening file, writing chunk).
   */
  async uploadFileFromString(t, i = "main.lua") {
    let e = t.replace(/\r/g, "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\t/g, "\\t").replace(/'/g, "\\'").replace(/"/g, '\\"');
    const a = this.getMaxPayload(!0) - 20;
    if (a <= 0)
      throw new Error("Max payload size too small for file upload operations.");
    const n = x(new TextEncoder().encode(e), a), o = await this.sendLua(`f=frame.file.open('${i}','w');print(1)`, { awaitPrint: !0 });
    if (o !== "1")
      throw new Error(`Failed to open file ${i} on device. Response: ${o}`);
    for (const d of n) {
      const h = await this.sendLua(`f:write("${d}");print(1)`, { awaitPrint: !0 });
      if (h !== "1")
        throw await this.sendLua("f:close();print(nil)", { awaitPrint: !0 }), new Error(`Failed to write chunk to ${i}. Response: ${h}`);
    }
    await this.sendLua("f:close();print(nil)", { awaitPrint: !0 });
  }
  /**
   * A convenience method that calls `uploadFileFromString`.
   * Uploads file content to a specified path on the Frame device.
   * @param fileContent The string content of the file to upload.
   * @param frameFilePath The path on the Frame device where the file will be saved. Defaults to "main.lua".
   * @returns A promise that resolves when the file upload is complete.
   */
  async uploadFile(t, i = "main.lua") {
    await this.uploadFileFromString(t, i);
  }
  /**
   * Sends a multi-packet message to the device.
   * This method handles chunking the payload and sending it in multiple BLE packets
   * according to a specific protocol (message code, total size header, then data chunks).
   *
   * On Frame, each packet is written and acknowledged by data.lua before the
   * next, and the promise resolves once the last packet is acknowledged.
   *
   * On Halo, packets are written without response, each filling whole
   * link-layer packets, with at most `receiveBufferSize / (packet size + 2)`
   * of them unacknowledged at a time. The promise resolves once the last
   * packet is written, so consecutive messages stream back to back; the
   * remaining ACKs are collected by the next call that talks to the device
   * (or by `waitForAcks()`). `awaitBtResponse` is ignored on Halo.
   *
   * The device app must acknowledge every packet with data.lua's ACK (data
   * payload 01 00 00), as data.lua's own handler does. On Halo, an app whose
   * receive callback doesn't makes a LATER call throw after its timeout (the
   * next sendLua or sendData, or a sendMessage once the window is full), not
   * this one; control signals just wait about a second and carry on.
   * @param msgCode A number (0-255) representing the message type or command.
   * @param payload The Uint8Array data to send as the message payload.
   * @param showMe If true, logs details of each transmitted packet to the console. Defaults to false.
   * @param awaitBtResponse Frame only: if true, uses write-with-response at the BLE level. Defaults to true.
   * @returns A promise that resolves when all parts of the message have been sent (and, on Frame, acknowledged).
   * @throws Error if msgCode is out of range, payload is too large, if max payload size is too small for the protocol,
   * or if the device fails, stops acknowledging or disconnects mid-message.
   */
  async sendMessage(t, i, e = !1, s = !0) {
    if (t < 0 || t > 255)
      throw new Error(`Message code must be 0-255, got ${t}`);
    const o = i.byteLength;
    if (o > 65535)
      throw new Error(`Payload size ${o} exceeds maximum 65535 bytes`);
    let d, h;
    if (this.deviceType === "Halo") {
      d = A(this.maxPayload) - 1;
      const m = Math.max(1, Math.floor(this.receiveBufferSize / (d + 2)));
      h = async (f) => {
        await this.awaitAcks(m - 1);
        const y = new Uint8Array(1 + f.byteLength);
        y[0] = 1, y.set(f, 1), this.acksOutstanding++;
        try {
          await this.transmit(y, e, !1);
        } catch (R) {
          throw this.acksOutstanding = Math.max(0, this.acksOutstanding - 1), R;
        }
      };
    } else
      d = this.getMaxPayload(!1), h = async (v) => {
        await this.sendData(v, { showMe: e, awaitData: !0, awaitBtResponse: s });
      };
    const l = d - 1 - 2, w = d - 1;
    if (l <= 0 || w <= 0)
      throw new Error("Max payload size too small for message sending protocol.");
    let u = 0;
    const p = Math.min(l, o), g = new Uint8Array(3 + p);
    for (g[0] = t, g[1] = o >> 8, g[2] = o & 255, g.set(i.subarray(0, p), 3), await h(g), u += p; u < o; ) {
      const v = o - u, m = Math.min(w, v), f = new Uint8Array(1 + m);
      f[0] = t, f.set(i.subarray(u, u + m), 1), await h(f), u += m;
    }
  }
}
function x(c, t) {
  if (t <= 0)
    throw new Error("maxChunkBytes must be positive.");
  const i = new TextDecoder(), e = [];
  let s = 0;
  for (; s < c.length; ) {
    let a = s + t;
    if (a >= c.length)
      a = c.length;
    else {
      for (; a > s && (c[a] & 192) === 128; )
        a--;
      let n = 0;
      for (; a - 1 - n >= s && c[a - 1 - n] === 92; )
        n++;
      if (n % 2 === 1 && a--, a === s)
        throw new Error("maxChunkBytes is too small to hold the next character of the payload.");
    }
    e.push(i.decode(c.subarray(s, a))), s = a;
  }
  return e;
}
export {
  C as BrilliantBle,
  D as BrilliantDeviceType,
  A as bleWriteSize,
  x as chunkLuaString
};
