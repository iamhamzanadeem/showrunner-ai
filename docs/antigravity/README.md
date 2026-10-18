# UI v3: built with the Antigravity CLI (`agy`)

UI v3 (static/index.html, static/style.css, static/app.js) was written by the Antigravity CLI. A reviewer drove it through 6 specs, each one invoked as
`agy [-c] -p "Read the spec file .../ui3_stepN.md and implement it" --dangerously-skip-permissions --effort high`.
After every step the reviewer checked the diff, ran the tests and took Playwright screenshots against a free mock server, then wrote the next spec with corrections.

- ui3_context.md: shared context, constraints, API, target layout
- ui3_step1.md: app shell, sidebar, chat, composer, empty state
- ui3_step2.md: the "Director is working…" thinking block, plus step-1 corrections
- ui3_step3.md: final cut mode and panel (player, timeline, shot detail, versions)
- ui3_step4.md: polish (cards, timeline fit, dark-mode contrast, 1280px)
- ui3_step5.md: timeline ruler/clip alignment, fit-to-width, ruler inset
- ui3_step6.md: simpler start screen (prompt cards + recent projects grid), no sample badges, newest-first sidebar
