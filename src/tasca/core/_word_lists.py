"""Word lists for human-readable ID generation.

This module contains the constant word lists used to generate memorable
identifiers. The lists are intentionally small and curated for:
- Positivity: all words have pleasant connotations
- Memorability: common, easy-to-spell words
- Safety: no offensive or controversial terms
- Uniqueness: words that combine well together

Design decision: These are constants in a separate module to:
1. Keep the main generation logic focused (single responsibility)
2. Allow easy updates to word lists without touching logic
3. Make the lists easily testable and verifiable
"""

# Adjectives: Safe, positive, memorable
# 40 words covering various pleasant qualities
ADJECTIVES: tuple[str, ...] = (
    "able",
    "active",
    "brave",
    "bright",
    "calm",
    "clever",
    "cool",
    "curious",
    "eager",
    "fair",
    "fine",
    "gentle",
    "glad",
    "good",
    "great",
    "happy",
    "kind",
    "keen",
    "light",
    "lively",
    "lucky",
    "nice",
    "polite",
    "proud",
    "quick",
    "quiet",
    "rare",
    "rich",
    "right",
    "safe",
    "sharp",
    "smart",
    "soft",
    "strong",
    "swift",
    "tall",
    "true",
    "warm",
    "wise",
    "witty",
)

# Nouns: Common animals, objects, concepts
# 40 words - predominantly animals (natural category)
NOUNS: tuple[str, ...] = (
    "ant",
    "bear",
    "bee",
    "bird",
    "camel",
    "cat",
    "deer",
    "dog",
    "duck",
    "eagle",
    "fish",
    "fox",
    "frog",
    "goat",
    "goose",
    "hawk",
    "horse",
    "lion",
    "llama",
    "moose",
    "mouse",
    "owl",
    "panda",
    "pig",
    "rabbit",
    "raven",
    "seal",
    "shark",
    "sheep",
    "snake",
    "swan",
    "tiger",
    "toad",
    "whale",
    "wolf",
    "zebra",
    "moon",
    "star",
    "tree",
    "wind",
)

# Verbs: Simple, common actions
# 20 words - actions that pair well with animals/nature
VERBS: tuple[str, ...] = (
    "dances",
    "dreams",
    "flies",
    "glides",
    "hops",
    "hunts",
    "jogs",
    "jumps",
    "leaps",
    "moves",
    "plays",
    "runs",
    "sings",
    "sleeps",
    "soars",
    "spins",
    "swims",
    "walks",
    "wanders",
    "waves",
)

# Total unique combinations: 40 * 40 * 20 = 32,000
# With suffix (1-9999): 32,000 * 9999 = 319,968,000
