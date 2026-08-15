# Book-specific people and term context prompt

Process only people and terms that have already entered canonical data. Cover every target in the `catalog context-plan` exactly once. For a person, make `description` one sentence stating who the person is as introduced in this book and which view, work, quotation, or evaluation the book connects to them. For a term, state the meaning or role it has in this book, not a general dictionary definition.

Do not add biography, general knowledge, evaluation, or causation absent from the manuscript. Paraphrase concisely in at most 500 characters; do not quote the book or provide enough detail to substitute for the chapter. When the evidence is sparse, give only the smallest supported account of the discussion in which the entry occurs.

Return exactly one JSON object in this shape:

```json
{
  "plan_id": "CTX-...",
  "candidates": [
    {
      "record_id": "NAME-...",
      "description": "The book introduces ... and refers to ... when discussing ... .",
      "evidence": [
        {
          "snippet": "an exact 8-500 character span from the manuscript",
          "occurrence": 0,
          "supports_field": "book_context"
        }
      ]
    },
    {
      "record_id": "TERM-...",
      "description": "In the book, this term denotes ... .",
      "evidence": [
        {
          "snippet": "an exact 8-500 character span from the manuscript",
          "occurrence": 0,
          "supports_field": "book_meaning"
        }
      ]
    }
  ]
}
```

Every evidence span must lie in the target's declared chapter, and at least one span must contain the target name or term itself. Add more than one span to the same field only when needed. Do not emit offsets, hashes, review decisions, acceptance, or approval. The toolkit rechecks source and chapter binding; a separate human or AI reviewer then checks meaning, attribution, and concision.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
