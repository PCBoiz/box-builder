# Writing rules for the app's text and prompts

Applies to the Vietnamese UI text, error messages, README, and the prompts that make a model write text
(`app/planner.py`). The picker prompt returns IDs only, so it is not covered.

1. **Say the thing.** No opener that announces the point ("Lưu ý rằng...") and no closer that repeats it.
2. **Name the specific.** "Thay 3 món" beats "Một số thay đổi đã được thực hiện". Give the number, the box, the object.
3. **Use a subject who acts.** "Bạn bấm Duyệt" beats "Hộp sẽ được duyệt".
4. **Plain words.** No marketing adjectives (mạnh mẽ, toàn diện, tối ưu, mượt mà, đa dạng, phong phú). No adverbs that only add emphasis.
5. **Absolutes only for hard guarantees.** "Không bao giờ ghi vào SM" is a guarantee and stays. "Luôn nhanh chóng" is padding.
6. **Do not force threes.** List the items there are. Two or four is fine.
7. **Errors say what happened, why, and what to do.** One short sentence each.
8. **One word per thing.** hộp (box), món (object), kho (catalog), Duyệt (approve), Nháp (draft). Do not rotate synonyms.
9. **No em dashes, no emoji, no exclamation marks** in UI text.

`tests/test_copy.py` checks rules 4 and 9 on the shipped text. The rest needs a human read.

## Limits

- The public rule sets are written for English. Rules 1 to 4, 7 and 8 carry over to Vietnamese. Word lists do not,
  so the Vietnamese list in the test is short and local.
- These patterns are signals. Humans on autopilot write the same way, and AI detectors are unreliable (false
  positives on non-native writers, and easy to beat by paraphrase). Write for the reader. Do not write to pass a detector.
- Tools that rewrite text to look "undetectable" (for example tryemulate.ai, a paid per-word service) solve a
  different problem. This project does not use them.

## Sources

- stop-slop (Hardik Pandya, MIT): https://github.com/hardikpandya/stop-slop
- avoid-ai-writing (Conor Bronsdon, MIT): https://github.com/conorbronsdon/avoid-ai-writing
- Wikipedia, Signs of AI writing: https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
