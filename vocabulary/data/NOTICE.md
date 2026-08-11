# Vocabulary Dataset Notice

`cefr_words.json` is a normalized vocabulary-level mapping derived from the
following sources. It is data, not application code.

## CEFR-J Wordlist Version 1.6

- Title: The CEFR-J Wordlist Version 1.6
- Compiler: Yukio Tono, Tokyo University of Foreign Studies (TUFS)
- Source: https://www.cefr-j.org/data/CEFRJ_wordlist_ver1.6.zip
- Terms: https://www.cefr-j.org/download.html
- Retrieved: 2026-08-11

Copyright belongs to Tono Laboratory at TUFS. The source terms permit research
and commercial use with proper acknowledgement, and permit modified wordlists
when the original wordlist is cited.

Requested citation:

> The CEFR-J Wordlist Version 1.6. Compiled by Yukio Tono, Tokyo University of
> Foreign Studies. Retrieved from https://www.cefr-j.org/download.html on
> 2026-08-11.

## Octanove Vocabulary Profile C1/C2 Version 1.0

- Creator: Octanove Labs
- Source: https://github.com/openlanguageprofiles/olp-en-cefrj
- Source file: `octanove-vocabulary-profile-c1c2-1.0.csv`
- License: Creative Commons Attribution-ShareAlike 4.0 International
- License URL: https://creativecommons.org/licenses/by-sa/4.0/
- Retrieved: 2026-08-11

## Modifications

The Filmocabulary mapping:

- expands slash-separated spelling variants;
- normalizes case, punctuation, whitespace, apostrophes, and hyphens;
- combines duplicate entries using the earliest recorded CEFR level;
- removes entries whose earliest recorded level is A1, A2, or B1;
- retains only effective B2, C1, and C2 entries; and
- stores the result as an alphabetically ordered JSON mapping.

The combined `cefr_words.json` dataset is distributed under CC BY-SA 4.0 and
remains subject to the CEFR-J acknowledgement terms above. This notice and the
dataset license do not change the license of Filmocabulary's application code.
