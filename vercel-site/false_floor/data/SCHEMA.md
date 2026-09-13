# Dataset schema

`public.jsonl` is the whole v0 dataset: 40 items, one JSON object per line, no
nesting. 20 twin pairs, 10 in `science` and 10 in `cyber_concept`.

## Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Unique item id. Convention is `<twin_id>_<split>`, e.g. `chem_01_benign`. |
| `twin_id` | string | Ties the two members of a pair together. Exactly two items share each `twin_id`. |
| `split` | string | `benign` or `safety_proxy`. |
| `domain` | string | `science` or `cyber_concept`. |
| `input` | string | The full question shown to the model: stem, then the four options on their own lines as `A) ... B) ... C) ... D) ...`. |
| `target` | string | The correct answer letter, one of `A`, `B`, `C`, `D`. |
| `choices` | list | Always `["A", "B", "C", "D"]`. This is the set of answer letters the scorer will accept, not the option text. The option text lives inside `input` so that the model sees one self-contained message. |
| `skill` | string | The underlying capability being measured. Both members of a pair carry the same value. |
| `notes` | string | Free text. Records the reasoning operation, the difficulty band, and either the twin-match rationale (benign items) or the dual-use rationale (safety-proxy items). |

`scripts/validate_dataset.py` enforces all of this. Run it after any edit.

## What the two splits mean

`benign` is ordinary science or ordinary security material. Nothing about the
item is safety-flavoured.

`safety_proxy` is the same capability pointed at a safety-relevant question:
which hazard class applies, which containment tier is required, which oversight
committee reviews the work, which control failure explains an outcome, how a
request should be classified under policy.

The name matters. These are not dangerous-capability items and this pack does
not contain any. They are proxies: items that *look* to a model like the kind of
thing a dangerous-capability evaluation would ask about, while remaining
conceptual. That resemblance is the whole point, because the experiment asks
whether a model treats safety-flavoured items differently when it is given a
reason to.

Earlier drafts of this pack called this split `danger`. The name was changed
because it overstated what is in the file. The task still accepts
`split=danger` as an alias so older commands keep working.

## The pairing rule, which is the load-bearing part

A pair is only useful if a drop in `safety_proxy` accuracy cannot be explained by
the item simply being harder or by the model knowing less about that subject.
So the two members of a pair are written to share:

- the same reasoning operation (apply a mole ratio, count half-lives, place an
  activity in a published framework, invert a relation against a threshold)
- the same `skill` value
- the same difficulty band, recorded in `notes`
- the same abstraction level, so a definitional item is never paired with an
  applied one
- similar linguistic complexity and similar length
- the same answer format and the same four-option structure

and to differ in one thing only: the safety-relevant valence.

This is hand-assessed, not empirically validated. It is the most likely place
for v0 to be wrong, and it is the first thing to attack when expanding the
suite. If a pair turns out to be mismatched, the honest fix is to rewrite or
drop the pair rather than to explain the gap away.

Holding items constant helps control fixed differences. It does not guarantee
that difficulty or wording cannot interact with a cue. Review matching before
attributing shifts to an incentive. See [review notes](../../docs/DATASET_REVIEW.md).
The validator intentionally enforces the current v0 shape; expansion requires
a versioned dataset and updated validation expectations.

## Answer position balance

Position bias is a real failure mode here: if a model favours one letter and the
two splits have different letter distributions, that alone produces a gap that
looks like selective underperformance. So targets are balanced deliberately.

Across all 40 items, each letter is correct exactly 10 times. Within each of the
four cells (domain x split, 10 items each) no letter is correct fewer than two
times. The validator checks both, and will fail on structural drift.

## Dual-use rule for `safety_proxy` items

Every safety-proxy item must be answerable from published safety, policy or
classification material, and must stay at the level of *which category, which
control, which committee, which severity band*.

Not allowed, in the item, the options, or the notes:

- synthesis routes, reagents, quantities, conditions or equipment lists
- pathogen handling, culture, enhancement or acquisition procedures
- exploit code, proof-of-concept payloads, working commands, target selection
- any content whose value to a reader is that it tells them how to do harm

Allowed:

- hazard and toxicity classification categories
- containment tiers and risk group definitions
- oversight and disclosure policy
- occupational exposure and radiation protection reasoning
- defensive detection concepts and control selection
- post-incident analysis of why an impact spread

The test to apply when writing a new item: if the correct answer were leaked to
someone who wanted to cause harm, would it help them? For everything in this
file the answer is no, because the answer is a category name, a committee, a
distance that keeps a worker safe, or a policy decision to decline.

## Adding items

1. Write the pair together, never one side at a time.
2. Give both members the same `skill` and the same difficulty band.
3. Read the two stems side by side and ask what a model would need to know for
   each. If the answer differs, the pair is not a pair.
4. Keep the safety-proxy member inside the dual-use rule above.
5. Set targets so the position balance still holds.
6. Run `python scripts/validate_dataset.py`.
