# Career Watcher: recording process

Investigating documents is only half the job — what you learn has to be recorded, or it's lost the moment this run ends. Follow this process as you work:

## While investigating

- Use `list_unprocessed_documents` to see what's left to work through — it's already filtered down to documents you haven't marked processed yet, so start there instead of `list_career_documents`.
- Use `read_document` to pull the text of anything worth investigating.

## After you finish with a document

Once you've fully investigated a document (and any leads it pointed you to), call `mark_processed(relpath)` for it. This records that the document has been reviewed, so a future run doesn't waste effort re-investigating something unchanged. Only mark a document processed once you're done extracting what's useful from it — not before.

## When you learn something

As you form an understanding worth keeping — a direct fact the evidence clearly supports, or a hedged inference reasoned from indirect evidence (e.g. a project's code implies familiarity with a technology the resume never mentions) — call `save_candidate_fact(text)` to record it for the stakeholder panel to review. You are not producing the final assessment — the panel debates and refines what you record — so each call should be one clear, self-contained piece of first-pass knowledge, written so it makes sense on its own later without the surrounding investigation context. State plainly whether it's a direct fact or an inference, and what evidence it's based on.

Don't wait until the end to save everything at once — record each piece of knowledge as you find it, in case the run is interrupted.
