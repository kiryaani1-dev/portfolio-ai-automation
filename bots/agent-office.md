# Agent Office — pixel-art office for the colony (in build)

- A pixel-art room where every live bot is a character: walks to its desk, types when writing code or
  text, goes to the file cabinet when reading files, to the computer when running commands, drinks
  coffee when idle. A plate above the head shows the real current task.
- Click on a character → its real chat/log opens.
- Built by **C.Q.** (the coder agent) via **opencode**; event-driven: a driver writes JSON events,
  a watcher turns them into NPC actions (plan adapted from the reference projects, MIT + attribution).
- Stage plan: repo skeleton → office with one NPC → own Hermes driver → map + sprites → optional
  text command layer ("ask the butler about vacancies") → packaging with screenshots and a demo video.
- Status: **in build** — priority after the colony itself is stable.

## The orchestrator (planned centerpiece)

A central **orchestrator bot** the human talks to, at the head of the office:

- **Voice communication** — the main interface: speak to the orchestrator like Jarvis.
- **Task delegation in the moment** — the orchestrator routes the request to the right agent right
  away ("ask the butler about vacancies", "start the montage for episode 3") and reports back.
- **Computer control by voice** — open programs, run commands, manage files by voice, with the
  orchestrator translating the command into agent actions.

This makes the office both a showcase and a usable control panel for the whole colony.

