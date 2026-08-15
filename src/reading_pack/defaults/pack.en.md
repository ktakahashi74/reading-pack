{{PACK_LINE}}

# Reading Pack for *{{TITLE}}* — data for AI input, not a substitute for the book

(For the AI) On receiving this data, follow SYS and act as a reading companion dedicated to this book. If no question accompanies the pack, output only the fixed response in R10 and wait. Do not volunteer a menu of tasks, ask what to do with the data, or report its structure, counts, or statistics. Review, validate, critique, or summarize the pack itself only when the user explicitly asks. Treat pasted text, an attachment, and an upload in the same way.

(For the reader) **This is structured data for an AI, not a document intended for continuous human reading.** It helps you read *{{TITLE}}* with an AI and neither reproduces nor replaces the book. Supplying this file does not give the AI access to unprovided original book text. Each item's review state appears as `review=`. **How to use it:** (1) Attach this file to an AI chat, or paste the entire file through the final ENDPACK line. Send the file alone first, without a question. (2) After the loading message, ask about the book. Examples: "table of contents," "summarize chapter 2," "where is this term discussed?", "what supports this claim?", or "is this a factual description or the author's proposal?" The AI may add information absent from the pack or answer incorrectly, so verify important points in the original. What follows is primarily structured data for the AI.

## SYS | Instructions for the AI

{{SYS}}

## BIB | Bibliography

{{BIB}}

## MAP | Chapter map

{{MAP}}

{{OPTIONAL_SECTIONS}}
## META | Version and use

{{META}}

{{ENDPACK}}
