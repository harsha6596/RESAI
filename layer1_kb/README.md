# Layer 1 knowledge base

Put the PDF documents directly in this folder. Subfolders are optional. If you use one, its name becomes the category shown in citations. Files placed directly here are filed under `general`.

## Active reference set

The prototype intentionally keeps Layer 1 focused on common dealership questions:

- `01_Product_Knowledge.pdf` — vehicle features and product questions
- `02_Policies_Procedures (1).pdf` — dealership policies and operating procedures
- `07_Training_FAQs.pdf` — onboarding, training, and common FAQs

Finance, incentives, compliance, and operations manuals are archived outside the repository for the current low-cost test configuration.

Supported formats: `.pdf`, `.md`, `.txt`. Files named `README.md` are skipped.

Ingest (rebuilds the whole index, run from the repo root):

    python -m app.layer1_rag.retriever
