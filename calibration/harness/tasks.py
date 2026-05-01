"""Task battery for the agent convention-confusion experiment.

Each task is a natural-language request that an LLM agent must fulfil
by selecting one tool from the presented cluster.

Design constraints:
  - Tasks for arm A (cyclic) and arm C (disambiguated) test the SAME intents
    so error rates are directly comparable.
  - Tasks for arm B (acyclic) test unambiguous intents to establish a
    baseline error rate (noise floor).
  - Each task has exactly one correct tool + correct parameter convention.
  - Convention traps document the plausible wrong choices.

Hidden dimensions tested:
  1. CONTENT_TYPE — is the file text or media?
  2. DEPRECATION — should the agent avoid the deprecated tool?
  3. OUTPUT_FORMAT — does the agent expect string or structured media output?
  4. PARTIAL_READ — does the agent use head/tail when appropriate?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HiddenDimension(Enum):
    CONTENT_TYPE = "content_type"
    DEPRECATION = "deprecation"
    OUTPUT_FORMAT = "output_format"
    PARTIAL_READ = "partial_read"


@dataclass(frozen=True)
class ProbeTask:
    task_id: str
    description: str  # natural-language request shown to the agent
    ground_truth_tool: str  # correct tool name for this arm
    ground_truth_params: dict  # correct parameters
    convention_traps: list[str] = field(default_factory=list)
    hidden_dimension: HiddenDimension = HiddenDimension.CONTENT_TYPE
    notes: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# ARM A (CYCLIC) TASKS — 30 tasks
#
# The agent sees: read_file, read_text_file, read_media_file
# Ground truth is determined by the REAL filesystem server behavior:
#   - text files → read_text_file
#   - media files → read_media_file
#   - read_file should never be chosen (deprecated)
# ═══════════════════════════════════════════════════════════════════════════════

CYCLIC_TASKS = [
    # ─── CONTENT_TYPE dimension: text files → read_text_file ──────────────────
    ProbeTask(
        task_id="cyc_01",
        description="Read the contents of /projects/app/config.yaml",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/projects/app/config.yaml"},
        convention_traps=["read_file (deprecated)", "read_media_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_02",
        description="Show me what's in /home/user/notes.md",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/home/user/notes.md"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_03",
        description="I need to see the source code in /src/main.py",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/src/main.py"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_04",
        description="Can you read /data/results.csv for me?",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/data/results.csv"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_05",
        description="Open /etc/nginx/nginx.conf and show me its contents",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/etc/nginx/nginx.conf"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_06",
        description="What's in the package.json at /app/package.json?",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/app/package.json"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_07",
        description="Read the log file at /var/log/application.log",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/var/log/application.log"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_08",
        description="Show me the Dockerfile at /deploy/Dockerfile",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/deploy/Dockerfile"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    # ─── CONTENT_TYPE dimension: media files → read_media_file ────────────────
    ProbeTask(
        task_id="cyc_09",
        description="Show me the image at /assets/logo.png",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/assets/logo.png"},
        convention_traps=["read_text_file (wrong type)", "read_file (deprecated + wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_10",
        description="Read the photo stored at /uploads/profile.jpg",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/uploads/profile.jpg"},
        convention_traps=["read_text_file (wrong type)", "read_file (deprecated + wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_11",
        description="I need to see the audio file at /media/recording.mp3",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/media/recording.mp3"},
        convention_traps=["read_text_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_12",
        description="Load the screenshot at /tmp/screenshot.png",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/tmp/screenshot.png"},
        convention_traps=["read_text_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_13",
        description="Get the GIF animation from /assets/loading.gif",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/assets/loading.gif"},
        convention_traps=["read_text_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_14",
        description="Read /sounds/notification.wav",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/sounds/notification.wav"},
        convention_traps=["read_text_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="cyc_15",
        description="Show me the icon file at /public/favicon.png",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/public/favicon.png"},
        convention_traps=["read_text_file (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    # ─── DEPRECATION dimension: tasks where agent must avoid read_file ────────
    ProbeTask(
        task_id="cyc_16",
        description="Read /docs/README.txt using the most appropriate tool",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/docs/README.txt"},
        convention_traps=["read_file (deprecated — schema identical but should not be used)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
        notes="Tests whether deprecation notice in description is respected",
    ),
    ProbeTask(
        task_id="cyc_17",
        description="Get the content of /config/settings.json",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/config/settings.json"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="cyc_18",
        description="I want to read a file. The path is /tmp/output.txt",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/tmp/output.txt"},
        convention_traps=["read_file (description says 'read file' matching user phrasing)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
        notes="User says 'read a file' — tests whether agent follows deprecated-tool name match",
    ),
    ProbeTask(
        task_id="cyc_19",
        description="Use the file reading tool to open /data/schema.sql",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/data/schema.sql"},
        convention_traps=["read_file (name matches 'file reading tool' phrasing)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
        notes="Phrasing biases toward read_file name; correct answer respects deprecation",
    ),
    ProbeTask(
        task_id="cyc_20",
        description="Read the file at /workspace/analysis.R",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/workspace/analysis.R"},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    # ─── PARTIAL_READ dimension: tasks requiring head/tail ────────────────────
    ProbeTask(
        task_id="cyc_21",
        description="Show me just the first 20 lines of /var/log/syslog",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/var/log/syslog", "head": 20},
        convention_traps=["read_file (deprecated, also has head param)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="cyc_22",
        description="What are the last 5 lines of /app/error.log?",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/app/error.log", "tail": 5},
        convention_traps=["read_file (deprecated, also has tail param)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="cyc_23",
        description="Read the first 10 lines of /src/index.ts",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/src/index.ts", "head": 10},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="cyc_24",
        description="Tail the last 50 lines from /logs/access.log",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/logs/access.log", "tail": 50},
        convention_traps=["read_file (deprecated)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    # ─── OUTPUT_FORMAT dimension: ambiguous extensions ─────────────────────────
    ProbeTask(
        task_id="cyc_25",
        description="Read /data/export.svg — I need to see the markup",
        ground_truth_tool="read_text_file",
        ground_truth_params={"path": "/data/export.svg"},
        convention_traps=["read_media_file (SVG is image-like but text-based)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
        notes="SVG is text (XML) but has media connotation — tests convention knowledge",
    ),
    ProbeTask(
        task_id="cyc_26",
        description="Get the contents of /assets/diagram.svg as an image",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/assets/diagram.svg"},
        convention_traps=["read_text_file (SVG is XML text)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
        notes="Same file type (SVG), but user explicitly wants image rendering",
    ),
    ProbeTask(
        task_id="cyc_27",
        description="Read the binary data from /firmware/update.bin",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/firmware/update.bin"},
        convention_traps=["read_text_file (user said 'read' but file is binary)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
        notes=".bin is binary — not text, not strictly media, but read_media_file is correct for non-text",
    ),
    ProbeTask(
        task_id="cyc_28",
        description="I need the raw content of /archive/data.tar.gz",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/archive/data.tar.gz"},
        convention_traps=["read_text_file (will corrupt binary)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="cyc_29",
        description="Open /reports/chart.png and read it",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/reports/chart.png"},
        convention_traps=["read_text_file (user said 'read')"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="cyc_30",
        description="Load the font file at /assets/fonts/roboto.woff2",
        ground_truth_tool="read_media_file",
        ground_truth_params={"path": "/assets/fonts/roboto.woff2"},
        convention_traps=["read_text_file (binary file)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# ARM B (ACYCLIC) TASKS — 30 tasks
#
# The agent sees: search_web, create_calendar_event, send_email
# Each task unambiguously maps to one tool. This measures baseline noise.
# ═══════════════════════════════════════════════════════════════════════════════

ACYCLIC_TASKS = [
    ProbeTask(
        task_id="acy_01",
        description="Find information about the latest Python release",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "latest Python release"},
    ),
    ProbeTask(
        task_id="acy_02",
        description="Search for how to configure nginx reverse proxy",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "configure nginx reverse proxy"},
    ),
    ProbeTask(
        task_id="acy_03",
        description="Look up the current price of Bitcoin",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "current Bitcoin price"},
    ),
    ProbeTask(
        task_id="acy_04",
        description="Search the web for React 19 new features",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "React 19 new features"},
    ),
    ProbeTask(
        task_id="acy_05",
        description="Find documentation for the Rust borrow checker",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "Rust borrow checker documentation"},
    ),
    ProbeTask(
        task_id="acy_06",
        description="What are the latest news about AI regulation in the EU?",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "AI regulation EU latest news"},
    ),
    ProbeTask(
        task_id="acy_07",
        description="Search for best practices in microservice architecture",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "microservice architecture best practices"},
    ),
    ProbeTask(
        task_id="acy_08",
        description="Look up how to use SQLAlchemy with async",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "SQLAlchemy async usage"},
    ),
    ProbeTask(
        task_id="acy_09",
        description="Find the weather forecast for Tokyo next week",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "Tokyo weather forecast next week"},
    ),
    ProbeTask(
        task_id="acy_10",
        description="Search for TypeScript 5.5 release notes",
        ground_truth_tool="search_web",
        ground_truth_params={"query": "TypeScript 5.5 release notes"},
    ),
    ProbeTask(
        task_id="acy_11",
        description="Schedule a team standup for tomorrow at 9am",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "Team Standup", "start_time": "2026-04-21T09:00:00"},
    ),
    ProbeTask(
        task_id="acy_12",
        description="Create a meeting called 'Project Review' next Monday at 2pm",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "Project Review", "start_time": "2026-04-27T14:00:00"},
    ),
    ProbeTask(
        task_id="acy_13",
        description="Add a 30-minute lunch reservation reminder for Friday noon",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={
            "title": "Lunch Reservation",
            "start_time": "2026-04-24T12:00:00",
            "duration_minutes": 30,
        },
    ),
    ProbeTask(
        task_id="acy_14",
        description="Put a dentist appointment on my calendar for May 1 at 10am",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "Dentist Appointment", "start_time": "2026-05-01T10:00:00"},
    ),
    ProbeTask(
        task_id="acy_15",
        description="Block off 2 hours for deep work tomorrow starting at 2pm",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={
            "title": "Deep Work",
            "start_time": "2026-04-21T14:00:00",
            "duration_minutes": 120,
        },
    ),
    ProbeTask(
        task_id="acy_16",
        description="Schedule a 1-on-1 with my manager for Wednesday at 3pm",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "1-on-1 with Manager", "start_time": "2026-04-22T15:00:00"},
    ),
    ProbeTask(
        task_id="acy_17",
        description="Create a calendar event for the company all-hands on April 28 at 11am",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "Company All-Hands", "start_time": "2026-04-28T11:00:00"},
    ),
    ProbeTask(
        task_id="acy_18",
        description="Add a gym session to my calendar for 6am tomorrow, 1 hour",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={
            "title": "Gym Session",
            "start_time": "2026-04-21T06:00:00",
            "duration_minutes": 60,
        },
    ),
    ProbeTask(
        task_id="acy_19",
        description="Schedule a code review session for Thursday at 4pm",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={"title": "Code Review", "start_time": "2026-04-23T16:00:00"},
    ),
    ProbeTask(
        task_id="acy_20",
        description="Put a reminder on my calendar: 'Submit expense report' Friday 5pm",
        ground_truth_tool="create_calendar_event",
        ground_truth_params={
            "title": "Submit Expense Report",
            "start_time": "2026-04-24T17:00:00",
        },
    ),
    ProbeTask(
        task_id="acy_21",
        description="Send an email to alice@company.com saying the deploy is done",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "alice@company.com",
            "subject": "Deploy Complete",
            "body": "The deploy is done.",
        },
    ),
    ProbeTask(
        task_id="acy_22",
        description="Email bob@team.org with the subject 'Meeting Notes' and attach the summary",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "bob@team.org",
            "subject": "Meeting Notes",
            "body": "Please find the meeting summary attached.",
        },
    ),
    ProbeTask(
        task_id="acy_23",
        description="Send a message to support@vendor.io asking about the API outage",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "support@vendor.io",
            "subject": "API Outage Inquiry",
            "body": "Could you provide an update on the API outage?",
        },
    ),
    ProbeTask(
        task_id="acy_24",
        description="Write an email to the team at eng@company.com about the sprint retrospective",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "eng@company.com",
            "subject": "Sprint Retrospective",
            "body": "Here are the notes from the sprint retrospective.",
        },
    ),
    ProbeTask(
        task_id="acy_25",
        description="Notify jane@partner.co that the contract is ready for review",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "jane@partner.co",
            "subject": "Contract Ready for Review",
            "body": "The contract is ready for your review.",
        },
    ),
    ProbeTask(
        task_id="acy_26",
        description="Send a thank-you email to mentor@university.edu",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "mentor@university.edu",
            "subject": "Thank You",
            "body": "Thank you for your guidance and support.",
        },
    ),
    ProbeTask(
        task_id="acy_27",
        description="Email ops@infra.net that the migration completed successfully",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "ops@infra.net",
            "subject": "Migration Complete",
            "body": "The migration completed successfully.",
        },
    ),
    ProbeTask(
        task_id="acy_28",
        description="Send a status update to pm@company.com: feature is 80% complete",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "pm@company.com",
            "subject": "Status Update",
            "body": "The feature is 80% complete.",
        },
    ),
    ProbeTask(
        task_id="acy_29",
        description="Email security@company.com about the vulnerability we found",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "security@company.com",
            "subject": "Vulnerability Report",
            "body": "We found a vulnerability that needs attention.",
        },
    ),
    ProbeTask(
        task_id="acy_30",
        description="Send a quick note to cto@startup.io: the demo is ready",
        ground_truth_tool="send_email",
        ground_truth_params={
            "to": "cto@startup.io",
            "subject": "Demo Ready",
            "body": "The demo is ready for your review.",
        },
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# ARM C (DISAMBIGUATED) TASKS — 30 tasks
#
# The agent sees: read_file_legacy, read_file_text, read_file_media
# SAME intents as arm A, SAME file paths. Only the tool names/descriptions differ.
# Ground truth mapping:
#   arm A read_text_file → arm C read_file_text
#   arm A read_media_file → arm C read_file_media
#   arm A read_file → arm C read_file_legacy (should never be chosen)
# ═══════════════════════════════════════════════════════════════════════════════

DISAMBIGUATED_TASKS = [
    ProbeTask(
        task_id="dis_01",
        description="Read the contents of /projects/app/config.yaml",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/projects/app/config.yaml"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_02",
        description="Show me what's in /home/user/notes.md",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/home/user/notes.md"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_03",
        description="I need to see the source code in /src/main.py",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/src/main.py"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_04",
        description="Can you read /data/results.csv for me?",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/data/results.csv"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_05",
        description="Open /etc/nginx/nginx.conf and show me its contents",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/etc/nginx/nginx.conf"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_06",
        description="What's in the package.json at /app/package.json?",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/app/package.json"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_07",
        description="Read the log file at /var/log/application.log",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/var/log/application.log"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_08",
        description="Show me the Dockerfile at /deploy/Dockerfile",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/deploy/Dockerfile"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_09",
        description="Show me the image at /assets/logo.png",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/assets/logo.png"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_10",
        description="Read the photo stored at /uploads/profile.jpg",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/uploads/profile.jpg"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_11",
        description="I need to see the audio file at /media/recording.mp3",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/media/recording.mp3"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_12",
        description="Load the screenshot at /tmp/screenshot.png",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/tmp/screenshot.png"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_13",
        description="Get the GIF animation from /assets/loading.gif",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/assets/loading.gif"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_14",
        description="Read /sounds/notification.wav",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/sounds/notification.wav"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_15",
        description="Show me the icon file at /public/favicon.png",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/public/favicon.png"},
        convention_traps=["read_file_text (wrong type)"],
        hidden_dimension=HiddenDimension.CONTENT_TYPE,
    ),
    ProbeTask(
        task_id="dis_16",
        description="Read /docs/README.txt using the most appropriate tool",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/docs/README.txt"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="dis_17",
        description="Get the content of /config/settings.json",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/config/settings.json"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="dis_18",
        description="I want to read a file. The path is /tmp/output.txt",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/tmp/output.txt"},
        convention_traps=["read_file_legacy (name matches 'read a file')"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="dis_19",
        description="Use the file reading tool to open /data/schema.sql",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/data/schema.sql"},
        convention_traps=["read_file_legacy"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="dis_20",
        description="Read the file at /workspace/analysis.R",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/workspace/analysis.R"},
        convention_traps=["read_file_legacy (deprecated)"],
        hidden_dimension=HiddenDimension.DEPRECATION,
    ),
    ProbeTask(
        task_id="dis_21",
        description="Show me just the first 20 lines of /var/log/syslog",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/var/log/syslog", "head": 20},
        convention_traps=["read_file_legacy (also has head param)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="dis_22",
        description="What are the last 5 lines of /app/error.log?",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/app/error.log", "tail": 5},
        convention_traps=["read_file_legacy (also has tail param)"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="dis_23",
        description="Read the first 10 lines of /src/index.ts",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/src/index.ts", "head": 10},
        convention_traps=["read_file_legacy"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="dis_24",
        description="Tail the last 50 lines from /logs/access.log",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/logs/access.log", "tail": 50},
        convention_traps=["read_file_legacy"],
        hidden_dimension=HiddenDimension.PARTIAL_READ,
    ),
    ProbeTask(
        task_id="dis_25",
        description="Read /data/export.svg — I need to see the markup",
        ground_truth_tool="read_file_text",
        ground_truth_params={"path": "/data/export.svg"},
        convention_traps=["read_file_media (SVG sounds like image)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="dis_26",
        description="Get the contents of /assets/diagram.svg as an image",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/assets/diagram.svg"},
        convention_traps=["read_file_text (SVG is XML)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="dis_27",
        description="Read the binary data from /firmware/update.bin",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/firmware/update.bin"},
        convention_traps=["read_file_text (user said 'read')"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="dis_28",
        description="I need the raw content of /archive/data.tar.gz",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/archive/data.tar.gz"},
        convention_traps=["read_file_text (will corrupt binary)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="dis_29",
        description="Open /reports/chart.png and read it",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/reports/chart.png"},
        convention_traps=["read_file_text (user said 'read')"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
    ProbeTask(
        task_id="dis_30",
        description="Load the font file at /assets/fonts/roboto.woff2",
        ground_truth_tool="read_file_media",
        ground_truth_params={"path": "/assets/fonts/roboto.woff2"},
        convention_traps=["read_file_text (binary file)"],
        hidden_dimension=HiddenDimension.OUTPUT_FORMAT,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# ARM D (OVERLAP CONTROL) TASKS — 30 tasks
#
# The agent sees: read_file_contents, write_file_contents, get_file_metadata
# All three share `path: string` — same parameter overlap as arms A and C.
# But the tools have NON-OVERLAPPING semantics (read vs write vs metadata):
# no hidden convention, no deprecation, no ambiguous content-type routing.
#
# This is the critical control. If arm D error rate ≈ arm B (acyclic/disjoint),
# then parameter overlap alone does NOT cause confusion — the hidden convention
# cycle is needed. If arm D ≈ arm A, then mere parameter overlap is sufficient
# to explain the confusion and the hidden-cycle story adds nothing.
# ═══════════════════════════════════════════════════════════════════════════════

OVERLAP_CONTROL_TASKS = [
    # ─── Read tasks (10) ─────────────────────────────────────────────────────
    ProbeTask(
        task_id="ovr_01",
        description="Show me the contents of /projects/app/config.yaml",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/projects/app/config.yaml"},
    ),
    ProbeTask(
        task_id="ovr_02",
        description="What's inside /home/user/notes.md?",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/home/user/notes.md"},
    ),
    ProbeTask(
        task_id="ovr_03",
        description="Read /src/main.py and show me the code",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/src/main.py"},
    ),
    ProbeTask(
        task_id="ovr_04",
        description="I need to see what's in /data/results.csv",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/data/results.csv"},
    ),
    ProbeTask(
        task_id="ovr_05",
        description="Open /etc/nginx/nginx.conf and display it",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/etc/nginx/nginx.conf"},
    ),
    ProbeTask(
        task_id="ovr_06",
        description="Read the package.json at /app/package.json",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/app/package.json"},
    ),
    ProbeTask(
        task_id="ovr_07",
        description="Show me /var/log/application.log",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/var/log/application.log"},
    ),
    ProbeTask(
        task_id="ovr_08",
        description="Display the Dockerfile at /deploy/Dockerfile",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/deploy/Dockerfile"},
    ),
    ProbeTask(
        task_id="ovr_09",
        description="I want to read /config/settings.json",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/config/settings.json"},
    ),
    ProbeTask(
        task_id="ovr_10",
        description="Can you show me the file at /workspace/analysis.R?",
        ground_truth_tool="read_file_contents",
        ground_truth_params={"path": "/workspace/analysis.R"},
    ),
    # ─── Write tasks (10) ────────────────────────────────────────────────────
    ProbeTask(
        task_id="ovr_11",
        description="Write 'hello world' to /tmp/greeting.txt",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/tmp/greeting.txt", "content": "hello world"},
    ),
    ProbeTask(
        task_id="ovr_12",
        description="Save this JSON to /config/new.json: {\"key\": \"value\"}",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/config/new.json", "content": '{"key": "value"}'},
    ),
    ProbeTask(
        task_id="ovr_13",
        description="Create a file at /docs/TODO.md with the content '# TODO'",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/docs/TODO.md", "content": "# TODO"},
    ),
    ProbeTask(
        task_id="ovr_14",
        description="Overwrite /tmp/output.txt with 'done'",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/tmp/output.txt", "content": "done"},
    ),
    ProbeTask(
        task_id="ovr_15",
        description="Put the text 'server started' into /var/log/status.txt",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/var/log/status.txt", "content": "server started"},
    ),
    ProbeTask(
        task_id="ovr_16",
        description="Write an empty string to /tmp/reset.flag",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/tmp/reset.flag", "content": ""},
    ),
    ProbeTask(
        task_id="ovr_17",
        description="Save 'export default {}' to /src/config.ts",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/src/config.ts", "content": "export default {}"},
    ),
    ProbeTask(
        task_id="ovr_18",
        description="Create /data/schema.sql with the content 'CREATE TABLE users (id INT);'",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/data/schema.sql", "content": "CREATE TABLE users (id INT);"},
    ),
    ProbeTask(
        task_id="ovr_19",
        description="Write the version string '2.0.1' to /app/VERSION",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/app/VERSION", "content": "2.0.1"},
    ),
    ProbeTask(
        task_id="ovr_20",
        description="Store 'PASS' in /results/test_output.txt",
        ground_truth_tool="write_file_contents",
        ground_truth_params={"path": "/results/test_output.txt", "content": "PASS"},
    ),
    # ─── Metadata tasks (10) ─────────────────────────────────────────────────
    ProbeTask(
        task_id="ovr_21",
        description="What's the file size of /data/dump.sql?",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/data/dump.sql"},
    ),
    ProbeTask(
        task_id="ovr_22",
        description="When was /config/settings.json last modified?",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/config/settings.json"},
    ),
    ProbeTask(
        task_id="ovr_23",
        description="Check the permissions on /etc/shadow",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/etc/shadow"},
    ),
    ProbeTask(
        task_id="ovr_24",
        description="How big is /uploads/backup.tar.gz?",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/uploads/backup.tar.gz"},
    ),
    ProbeTask(
        task_id="ovr_25",
        description="Get info about /var/log/syslog — size and modification time",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/var/log/syslog"},
    ),
    ProbeTask(
        task_id="ovr_26",
        description="What are the file details for /app/package.json?",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/app/package.json"},
    ),
    ProbeTask(
        task_id="ovr_27",
        description="Tell me the creation date of /docs/README.md",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/docs/README.md"},
    ),
    ProbeTask(
        task_id="ovr_28",
        description="Is /tmp/lockfile writable? Check its permissions",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/tmp/lockfile"},
    ),
    ProbeTask(
        task_id="ovr_29",
        description="How large is the database file at /data/app.db?",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/data/app.db"},
    ),
    ProbeTask(
        task_id="ovr_30",
        description="Get the modification timestamp of /deploy/docker-compose.yml",
        ground_truth_tool="get_file_metadata",
        ground_truth_params={"path": "/deploy/docker-compose.yml"},
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience mapping
# ═══════════════════════════════════════════════════════════════════════════════

TASKS_BY_ARM = {
    "cyclic": CYCLIC_TASKS,
    "acyclic": ACYCLIC_TASKS,
    "disambiguated": DISAMBIGUATED_TASKS,
    "overlap_control": OVERLAP_CONTROL_TASKS,
}
