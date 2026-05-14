# Ossature Documentation

This directory builds the site at https://docs.ossature.dev via the
[mkdocs.yml](../mkdocs.yml) at the repo root. The file you are reading
is not part of the published site, it is a note for people writing or
editing docs.

Ossature's docs follow the [Diátaxis framework](https://diataxis.fr/),
which splits documentation into four buckets based on what the reader
needs at the moment they hit the page. Plus a small First Steps section
on top for onboarding, and a single FAQ page for the questions that
keep coming up. Before adding a new page somewhere, take a minute to
check it actually belongs there.

## first-steps/

Onboarding for someone who has never used Ossature. Two pages only,
Installation and Your First Project. Goal is to get a complete novice
to a working install and one successful run as fast as possible.
Anything optional, anything that asks the reader to choose between
options, and anything that explains why something exists belongs in a
different section.

## tutorials/

Step-by-step lessons. Someone follows along and ends with something
they built themselves. Tutorials are concrete, they use specific
example projects rather than "your project", and they are complete
with no skipped steps. Narrow recipes for one problem, conceptual
essays, and lookup tables go elsewhere.

## topics/

Background reading. Someone here is trying to understand the system,
not finish a task. Design decisions, mental models, how a piece works
under the hood. Imperative "do this" instructions and exhaustive
option lists do not go here, those belong in how-to or reference.

## how-to/

Task-oriented recipes. Someone has a specific problem and wants the
solution. Each page solves one specific problem, like splitting a
large spec, and assumes the reader already has the basics. Skip the
introductory material and the long conceptual lead-up.

## reference/

Technical descriptions of the machinery. CLI commands, file formats,
configuration keys, error codes. Dry and complete. The reader already
knows what they are looking up, so prose tutorials, design rationale,
and recommendations on when to use each option go in topics, tutorials,
or how-to instead.

## faq.md

A single page for the questions that come up often and do not fit
anywhere else.

## When you are not sure where a page goes

The Diátaxis test is to ask what the reader needs from the page right
now. Someone learning without a specific goal wants a tutorial. Someone
trying to accomplish a specific goal wants a how-to. Someone trying to
understand why or how something works wants a topic page. Someone
looking up a fact wants reference. If the page you have in mind would
genuinely answer two of these, split it into two pages.

## Conventions

File names are lowercase and kebab-case, like `how-the-build-works.md`.
Every page starts with an H1 that matches the file's intent. Internal
links are relative. Code blocks declare a language for highlighting.
"Next Steps" footers fit in tutorials and first-steps but feel out of
place in reference or how-to, so leave them off there.
