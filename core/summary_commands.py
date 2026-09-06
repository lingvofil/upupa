"""Public names for the two modes of the same summary feature."""

SUMMARY_COMMANDS = ("чобыло", "упупа чобыло")
CATCHUP_COMMANDS = ("что я пропустил", "что я пропустила",
                    "упупа что я пропустил", "упупа что я пропустила")
ALL_SUMMARY_COMMANDS = SUMMARY_COMMANDS + CATCHUP_COMMANDS


def summary_mode(text):
    command = (text or "").strip().casefold().rstrip(" !?.,")
    if command in SUMMARY_COMMANDS:
        return "recent"
    if command in CATCHUP_COMMANDS:
        return "catchup"
    return None
