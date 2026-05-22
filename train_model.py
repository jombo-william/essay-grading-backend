import pandas as pd
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
import os

# ── Load base model ───────────────────────────────────────────────────────────
BASE_MODEL    = os.path.join("models", "all-MiniLM-L6-v2")
OUTPUT_MODEL  = os.path.join("models", "essay-grader-finetuned")

model = SentenceTransformer(BASE_MODEL)

# ── Load your dataset ─────────────────────────────────────────────────────────
df = pd.read_csv("training_data.csv")

# ── Build training pairs ──────────────────────────────────────────────────────
# Each pair: (assignment context, essay text) with a similarity score 0.0-1.0
# based on the label

LABEL_TO_SCORE = {
    "excellent":    1.0,
    "good":         0.80,
    "satisfactory": 0.60,
    "weak":         0.35,
    "poor":         0.15,
    "off_topic":    0.0,
}

train_examples = []
for _, row in df.iterrows():
    # Reference = title + instructions
    reference = f"{row['assignment_title']}. {row['assignment_title']}. {row['instructions']}"
    essay     = str(row['essay_text'])
    score     = LABEL_TO_SCORE.get(row['label'], 0.5)

    train_examples.append(InputExample(
        texts=[reference, essay],
        label=float(score),
    ))

print(f"✅ Loaded {len(train_examples)} training examples")

# ── Train ─────────────────────────────────────────────────────────────────────
train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=16)
train_loss       = losses.CosineSimilarityLoss(model)

model.fit(
    train_objectives=[(train_dataloader, train_loss)],
    epochs=10,               # increase to 20+ if you have more data
    warmup_steps=10,
    output_path=OUTPUT_MODEL,
    show_progress_bar=True,
)

print(f"✅ Fine-tuned model saved to: {OUTPUT_MODEL}")