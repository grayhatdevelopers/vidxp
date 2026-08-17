# VidXP documentation writing standard

Apply this standard to human-facing repository documentation. Its purpose is
reader comprehension, not merely consistent formatting.

## Start from the reader's situation

- State what the reader can accomplish or decide before explaining internals.
- Make prerequisites visible before the action that depends on them.
- State the expected result after a command or procedure when it is not obvious.
- Assume only the knowledge appropriate to the audience in the documentation
  map.
- Define a necessary technical term at first use. Replace an unnecessary term
  with product language.

An `Audience:` label, introductory disclaimer, or heading does not make dense
contract prose suitable for that audience.

## Give the document a deliberate flow

- Organize procedures in the order the reader performs them.
- Organize reference material around the choices or interfaces readers look up.
- Keep rationale next to the decision it explains, without interrupting a
  procedure with unrelated architecture.
- Make headings describe a task, decision, or subject. Avoid headings that are
  meaningful only to someone who already knows the implementation.
- Give each paragraph one coherent purpose and connect it to the surrounding
  paragraphs.
- Use bullets for alternatives, requirements, and short independent facts. Use
  numbered lists only when order matters.
- Prefer a table when readers repeatedly compare the same fields. Do not use a
  table to disguise long prose fragments.

## Write direct prose

- Prefer active voice and concrete subjects.
- Put the main clause before qualifications when accuracy permits.
- Split sentences that carry several independent conditions or outcomes.
- Remove throat-clearing, repeated claims, and generic filler.
- Avoid compressed noun chains such as “candidate runtime probe contract.” Say
  who performs the action and what happens.
- Use “you” for actions the reader performs and the component name for actions
  the software performs.
- Preserve necessary qualifications; clarity is not permission to overstate a
  guarantee.

Do not enforce readability through a mechanical sentence-length limit. A short
sentence can still be undefined or badly ordered, and a longer sentence can be
clear when its relationships are explicit.

## Present technical material where it helps

- Put a command immediately after the instruction it carries out.
- Keep command sequences complete and copyable; do not omit required setup,
  model preparation, activation, authentication, or verification.
- Explain placeholders before or directly after the command.
- Show internal identifiers only when readers must type, configure, inspect, or
  implement them.
- Separate normal operation from troubleshooting and implementation detail.
- Scope warnings precisely: state the affected setup, consequence, and action.
- Use links for deeper detail instead of inserting an abbreviated architecture
  document into a user guide.

## Review as a fresh reader

Read the changed document without relying on the implementation discussion and
answer:

1. Who is this for, based on the prose rather than metadata?
2. What can the reader accomplish or learn?
3. What must already be installed, configured, or understood?
4. What should the reader do next?
5. What result indicates success?
6. Which terms or transitions require knowledge found only elsewhere?
7. Can any paragraph be removed without losing useful information?
8. Did a reduction remove information that now has no canonical owner?

Revise the document when these answers are missing, scattered, contradictory,
or dependent on unstated context.
