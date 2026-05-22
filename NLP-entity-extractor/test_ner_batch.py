import os
import sys
import fitz  # PyMuPDF

sys.path.insert(0, r"c:\Users\user2\Documents\Cursor\OCR\OCR-document-parser\src")
from ocr_platform.profiles.court_decision_nlp.transformer import TransformerTokenClassifierExtractor

docs_dir = r"c:\Users\user2\Documents\Cursor\OCR\документы\решения25"
ner_model_path = r"c:\Users\user2\Documents\Cursor\OCR\NLP-entity-extractor\models\exports\ner"

def main():
    if not os.path.exists(ner_model_path):
        print("Bootstrapping NER model...")
        extractor = TransformerTokenClassifierExtractor.bootstrap_local_model(ner_model_path)
    else:
        print("Loading NER model...")
        extractor = TransformerTokenClassifierExtractor.from_pretrained(ner_model_path)
    
    pdf_files = [f for f in os.listdir(docs_dir) if f.lower().endswith('.pdf')]
    print(f"Found {len(pdf_files)} PDF files in {docs_dir}")
    print("=" * 80)
    
    with open("ner_batch_results.txt", "w", encoding="utf-8") as f:
        for idx, filename in enumerate(pdf_files, 1):
            filepath = os.path.join(docs_dir, filename)
            
            text = ""
            try:
                with fitz.open(filepath) as doc:
                    for page in doc:
                        text += page.get_text()
            except Exception as e:
                print(f"[{idx}/{len(pdf_files)}] Error reading {filename}: {e}")
                continue
            
            # Predict
            res = extractor.predict(text)
            
            fields = res.fields
            c_name = fields.court_name
            a_fio = fields.applicant_fio.normalized if fields.applicant_fio else None
            j_fio = fields.judge_fio.normalized if fields.judge_fio else None
            
            out_str = f"[{idx}/{len(pdf_files)}] {filename}\n"
            out_str += f"  Court: {c_name}\n"
            out_str += f"  Debtor: {a_fio}\n"
            out_str += f"  Judge: {j_fio}\n"
            
            print(out_str.strip(), flush=True)
            f.write(out_str + "\n")
            f.flush()

    print("=" * 80)
    print("Done! Results saved to ner_batch_results.txt")

if __name__ == "__main__":
    main()
