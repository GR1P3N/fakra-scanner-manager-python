# Fakra - Scanner manager
The application mediates between two barcode scanners (“wire” and “box”) and two Arduino keyboard emulator (EPT and Packing), while displaying each processing step in a table:

## Scanning
- A background `ScannerThread` continuously polls the “wire” and “box” serial ports.
- It first waits for a wire barcode, then for a box barcode.

## Queueing
- Each wire–box pair becomes a `QueueItem` and is appended to a FIFO queue.
- A new row appears in the table with an initial countdown of 7 seconds (`PROCESSING_TIME`).

## Countdown & Processing
- A global Qt timer fires every second, decrementing the remaining time for each queued item.
- When an item’s timer reaches zero, processing begins:
    - The box ID is sent to the Packing Arduino Keyboard Emulator
    - After a 500 ms delay, the wire ID is also sent to Packing

## Removal
- Once both messages have been sent, the row is removed from the table and the item is dropped from the queue.

## Scan Limit & Reset
- Each batch allows up to 5 scans (`SCAN_LIMIT`).
- If you hit the limit, the status changes to “Limit reached!”
- Pressing “New Box” clears the queue, resets the counter, and starts a fresh cycle.

#
Throughout, status messages and background colors guide the operator: purple when waiting for a wire, blue when waiting for a box or after resetting, and dynamic text during processing.