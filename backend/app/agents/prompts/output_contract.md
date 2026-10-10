# Output contract

Return exactly **one** JSON object matching the response schema supplied with
this request.

- The first character must be `{` and the last character must be `}`.
- Do not use Markdown. Do not wrap the object in code fences.
- Do not add any text before or after the JSON object.
- Do not repeat the decision, restate the question, or summarise what you are
  about to do.
- Do not explain the schema, describe the fields, or list the options.
- Do not output internal reasoning, deliberation, or chain-of-thought.
- Do not emit alternatives or multiple candidate answers.
- Keep every free-text field to short, evidence-grounded sentences.
- Use the exact field names from the schema. Do not invent fields or aliases.
- Stop immediately after the closing brace.

The schema is enforced by the provider while the response is generated, so it
does not need to be reproduced in your answer.
