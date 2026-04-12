"""FinBERT sentiment analysis for financial text.

Lazy-loads ProsusAI/finbert on first use. The model is cached after
the initial download so subsequent loads are instant.
Pre-loaded at startup via preload() to avoid cold-start latency.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

logger = logging.getLogger(__name__)

_model: "AutoModelForSequenceClassification | None" = None
_tokenizer: "AutoTokenizer | None" = None


def _load_model() -> tuple["AutoModelForSequenceClassification", "AutoTokenizer"]:
    """Load FinBERT model and tokenizer (singleton)."""
    global _model, _tokenizer
    if _model is not None and _tokenizer is not None:
        return _model, _tokenizer

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    logger.info("Loading FinBERT model...")
    _tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    _model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
    _model.eval()
    logger.info("FinBERT loaded successfully")
    return _model, _tokenizer


def preload():
    """Pre-load the model at startup to avoid cold-start latency."""
    _load_model()


def score_sentiment(text: str) -> float:
    """Return a sentiment score in [-1.0, 1.0] for a financial text.

    Maps FinBERT's softmax probabilities to a continuous score:
      score = P(positive) - P(negative)
    """
    import torch

    model, tokenizer = _load_model()
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = model(**inputs)
    probs = torch.nn.functional.softmax(outputs.logits, dim=-1)[0]
    # FinBERT labels: positive=0, negative=1, neutral=2
    p_pos = probs[0].item()
    p_neg = probs[1].item()
    return round(p_pos - p_neg, 3)


def score_batch(texts: list[str], batch_size: int = 32) -> list[float]:
    """Score a list of texts efficiently in batches.

    Returns a list of sentiment scores in [-1.0, 1.0].
    """
    import torch

    if not texts:
        return []

    model, tokenizer = _load_model()
    scores: list[float] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = tokenizer(
            batch,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            outputs = model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        for j in range(len(batch)):
            p_pos = probs[j][0].item()
            p_neg = probs[j][1].item()
            scores.append(round(p_pos - p_neg, 3))

    return scores
