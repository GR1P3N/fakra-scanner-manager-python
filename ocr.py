import cv2
import pytesseract

def debug_and_ocr(frame, x, y, w, h):
    # 1) Save the raw ROI
    roi = frame[y:y+h, x:x+w]

    # 2) Preprocess
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)

    # 3) Threshold plain vs inverted
    _, plain = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    _, inv   = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    # 4) Pick the clearer one (például itt mindig plain)
    proc = plain

    # 5) OCR with single-line mode
    config = '--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789/'
    text = pytesseract.image_to_string(proc, config=config).strip()
    return text

def main():
    x, y, w, h = 200, 200, 80, 50

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError("Nem sikerült megnyitni a kamerát")

    # Bemelegítés
    for _ in range(30):
        cap.read()

    win_name = "Kamerakép – nyomd meg az 's'-t az OCR-hez, 'q'-t a kilépéshez"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, 1280, 720)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Nem érkezett kép a kameráról")
            break

        # Preview és ROI kirajzolása
        preview = frame.copy()
        cv2.rectangle(preview, (x, y), (x+w, y+h), (0, 255, 0), 2)
        cv2.imshow(win_name, preview)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            # OCR lefuttatása
            print("Keresés...")
            text = debug_and_ocr(frame, x, y, w, h)
            print("Felismert szöveg:", text or "[nincs szöveg]")
        elif key == ord('q'):
            # Kilépés
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
