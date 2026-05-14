import base64

with open("PDF_not_read/1.pdf", "rb") as f:
    b64 = base64.b64encode(f.read()).decode("utf-8")
print(b64)

with open("test_decoded.pdf", "wb") as f:
    f.write(base64.b64decode(b64))

# with open("scan.pdf", "rb") as f:
#     b64_pdf = base64.b64encode(f.read()).decode()
# print(b64_pdf)