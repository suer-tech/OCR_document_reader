import os
import fitz  # PyMuPDF
import re
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline

model_path = r"c:\Users\user2\Documents\Cursor\OCR\NLP-entity-extractor\models\exports\early-report-classifier"
docs_dir = r"c:\Users\user2\Documents\Cursor\OCR\документы\решения25"

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def process_document(text: str, cls_pipeline) -> tuple[str, float, str]:
    normalized_text = normalize_whitespace(text)
    match = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", normalized_text, re.IGNORECASE)
    search_text = normalized_text[match.start():] if match else normalized_text

    sentences = [s.strip() for s in re.split(r'[.;]', search_text) if s.strip()]

    report_sentences = []
    for s in sentences:
        s_lower = s.lower()
        has_actor = "управляющ" in s_lower or "арбитражн" in s_lower or "финансов" in s_lower
        has_object = any(w in s_lower for w in ["отчет", "документ", "заблаговремен", "результат"])
        
        is_debtor_action = any(w in s_lower for w in [
            "передать финансов", "передать арбитражн", "документацию должника", "документации должника",
            "банковские карты", "печати", "штампы", "передать управляющ", 
            "уведомить финансов", "выдать финансов"
        ])
        
        if has_actor and has_object and not is_debtor_action:
            report_sentences.append(s)

    context_text = " ".join(report_sentences) if report_sentences else search_text[:500]
    
    pred = cls_pipeline(context_text, truncation=True, max_length=512)[0]
    return pred['label'], pred['score'], context_text

def main():
    print("Loading model...")
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForSequenceClassification.from_pretrained(model_path)
    model.eval()
    cls_pipeline = pipeline("text-classification", model=model, tokenizer=tokenizer, device=-1)
    
    pdf_files = [f for f in os.listdir(docs_dir) if f.lower().endswith('.pdf')]
    print(f"Found {len(pdf_files)} PDF files in {docs_dir}")
    print("=" * 80)
    
    required_count = 0
    not_required_count = 0
    required_results = []

    for idx, filename in enumerate(pdf_files, 1):
        filepath = os.path.join(docs_dir, filename)
        
        # Извлекаем текст
        text = ""
        try:
            with fitz.open(filepath) as doc:
                for page in doc:
                    text += page.get_text()
        except Exception as e:
            print(f"[{idx}/{len(pdf_files)}] Error reading {filename}: {e}")
            continue
            
        label, score, context = process_document(text, cls_pipeline)
        
        if label == "REQUIRED":
            required_count += 1
            required_results.append((filename, score, context))
            print(f"[{idx}/{len(pdf_files)}] {filename}")
            print(f"  --> REQUIRED ({score:.4f})")
        else:
            not_required_count += 1
            print(f"[{idx}/{len(pdf_files)}] {filename}")
            print(f"  --> NOT_REQUIRED ({score:.4f})")
            # print(f"  --> Context used: '{context[:200]}...'\n")
            
    print("=" * 80)
    print(f"SUMMARY: {required_count} REQUIRED, {not_required_count} NOT_REQUIRED")
    
    with open("batch_results.txt", "w", encoding="utf-8") as f:
        f.write(f"SUMMARY: {required_count} REQUIRED, {not_required_count} NOT_REQUIRED\n\n")
        f.write("=== REQUIRED DOCUMENTS ===\n")
        for res in required_results:
            f.write(f"File: {res[0]}\nScore: {res[1]:.4f}\nContext: {res[2]}\n\n")

if __name__ == "__main__":
    main()
