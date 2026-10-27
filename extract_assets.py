import fitz
import os

os.makedirs('extracted_assets', exist_ok=True)
pdf_path = r'C:\Users\anisk\.gemini\antigravity\brain\ca21b8ac-3bcf-4d82-a7a1-b0e8f694a646\.user_uploaded\media_1790953667503.pdf'
doc = fitz.open(pdf_path)

for p in range(len(doc)):
    page = doc[p]
    imgs = page.get_images()
    for idx, img in enumerate(imgs):
        xref = img[0]
        base_img = doc.extract_image(xref)
        ext = base_img['ext']
        fname = f"extracted_assets/page_{p+1}_img_{idx+1}.{ext}"
        with open(fname, 'wb') as f:
            f.write(base_img['image'])
        print(f"Page {p+1} img {idx+1}: {fname}, size {base_img['width']}x{base_img['height']}")
