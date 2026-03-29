import PyPDF2

with open("reglas.txt", "w", encoding="utf-8") as f:
    reader = PyPDF2.PdfReader("Documentacion/Reglas de negocio v2.pdf")
    for page in reader.pages:
        f.write(page.extract_text() + "\n\n")
