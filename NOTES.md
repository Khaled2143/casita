# Notes on my changes

## What I did

Casita ranked listings around one household's life: two large dogs, trails, beaches, bakeries. That was hardcoded across the ranking code. I made the ranking configurable per person, so the same listing set can be re-ranked for anyone's priorities.

I added a second profile for my own use case. If I took this job and was apartment hunting, I'd care about three things: how close a gym is, how close a halal market is, and the commute to downtown (I used the Financial District as the meetup/office anchor). So there are now two profiles: the original household (unchanged, still the default) and mine.

## Why

Casita started as a personal tool shaped around one person. The interesting question is whether it generalizes. Making the priorities swappable is the answer: the app isn't tied to one life anymore, it ranks for whoever's looking. The original profile stays as the default so nothing about the existing behavior changes, and the second profile proves the abstraction holds for a completely different person.

## How it works

A profile bundles everything about how one household ranks: which categories matter and how much, whether dog policy is a hard gate, and the Gemini prompt used for live ranking. Swapping the profile swaps the whole ranking behavior, not just part of it. You pick one with `--profile` on `demo`/`enrich`, or the `CASITA_PROFILE` env var.

On top of that, the index page has a live picker. Toggle a category (gym, halal market, commute, etc.) and the grid re-ranks in the browser, closest-first, and highlights any listing within 10 minutes of what you picked. This runs entirely client-side because Casita is a static site with no live backend. At render time the server embeds each listing's distance to all six categories as JSON in the page, and the picker reads that to re-rank without a network call. So it works the same on the offline demo.

## The one tradeoff worth calling out

The scoring math exists in two places: `rank.py` on the server and the picker's JavaScript in the browser. That's a DRY violation, but it's a real constraint of a static site: the browser has to re-rank without a server, so it needs its own copy of the formula. I did reduce it: the weights and sweet-spots now come from one source (the profile), embedded in the page and read by the JS, so the numbers can't drift. The arithmetic is still written twice. If I took this further I'd emit the whole scoring as data, or run it through one shared runtime, so there's a single source for the logic too. I left it as-is because it works and I didn't want to risk regressions on a short timeline.
