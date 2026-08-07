# Product

## Register

product

## Users

Technical users who run Vegapunk as a personal terminal assistant. They work primarily from the
keyboard, expect agent actions to remain legible while they stay in flow, and need a clear sense of
what the agent may do in the current session.

## Product Purpose

Vegapunk is a local-first, persistent CLI agent that helps users complete real work through model
providers, workspace tools, memory, reusable skills, and scheduled tasks. Success means the agent is
capable without becoming opaque: users can move quickly, understand its current state, and retain
control over side effects.

## Brand Personality

Capable, direct, and restrained. The interface should have the clarity and status awareness of a
mature developer tool while retaining Vegapunk's distinctive color and personality.

## Anti-references

- Decorative terminal chrome that competes with the task.
- Dangerous state changes communicated only through color or a transient message.
- Approval flows that interrupt more often than the selected safety mode requires.
- An exact visual imitation of Codex or another agent instead of a Vegapunk-native interpretation.
- Hidden shortcuts or modes that require documentation before the interface can be used safely.

## Design Principles

1. Make consequential state persistent and unmistakable.
2. Keep the keyboard workflow fast, familiar, and reversible.
3. Reveal detail when it helps a decision; otherwise stay out of the way.
4. Preserve terminal-native behavior across rich, plain, piped, and no-color environments.
5. Borrow proven interaction patterns without losing Vegapunk's identity.

## Accessibility & Inclusion

The product is keyboard-first. Every important state must have a textual label and remain readable
with `NO_COLOR`, in plain output, and for users who cannot distinguish semantic colors. Focus and
mode changes must not depend on animation, pointer input, or color alone.
