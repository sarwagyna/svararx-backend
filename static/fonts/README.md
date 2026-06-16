# Telugu Font Setup for VaniRx Prescriptions

This directory should contain the `NotoSansTelugu-Regular.ttf` font file for rendering Telugu text in patient instruction PDFs.

## Installation Steps

1. **Download the font from Google Fonts:**
   - Visit: https://fonts.google.com/noto/specimen/Noto+Sans+Telugu
   - Click "Download family"

2. **Extract the downloaded file:**
   - Unzip the downloaded file (usually named something like `Noto_Sans_Telugu.zip`)

3. **Copy the Regular weight font:**
   - Locate the file named `NotoSansTelugu-Regular.ttf` or `NotoSansTelugu[wght]-Regular.ttf`
   - Copy it to this directory: `backend/static/fonts/`
   - Rename it to exactly: `NotoSansTelugu-Regular.ttf`

4. **Verify the installation:**
   - The file should be at: `backend/static/fonts/NotoSansTelugu-Regular.ttf`
   - Restart the backend server
   - Generate a prescription PDF and verify Telugu text renders correctly

## Alternative: Use Google Fonts CDN (Online rendering)

If you're using a web-based PDF rendering service instead of ReportLab, you can use:
```html
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Telugu&display=swap" rel="stylesheet">
```

## Troubleshooting

- If the font file is not found, the system will fall back to Helvetica (Telugu text may not render properly)
- Check the backend logs for any font loading errors
- Ensure the file permissions allow the application to read the TTF file

## References

- Google Fonts Noto Sans Telugu: https://fonts.google.com/noto/specimen/Noto+Sans+Telugu
- ReportLab Font Documentation: https://www.reportlab.com/docs/reportlab-userguide.pdf
