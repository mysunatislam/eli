"""Deterministic intent rules. They make common commands instant and free, and they are
the whole brain when no LLM key is configured."""
from __future__ import annotations

import re

WAKE_PREFIX = re.compile(
    r"^(?:hello |hey |hi |ok |okay |just )?"
    r"(?:[iea]+l+[ieya]+|allie|ali|ally|i lie|i lay|inlet)"
    r"[,!.]*\s*",
    re.I
)


def strip_wake(text: str) -> str:
    from .speech.stt import normalize_speech_text
    norm = normalize_speech_text(text)
    return WAKE_PREFIX.sub("", norm.strip())


PATTERNS: list[tuple[str, re.Pattern]] = [
    ("forget_all", re.compile(r"^forget (?:everything|all|it all)(?: about me)?[.!]?$", re.I)),
    ("forget_today", re.compile(r"^forget (?:everything (?:about|from) )?today[.!]?$", re.I)),
    ("forget", re.compile(r"^forget (?:that |about )?(.+?)[.!]?$", re.I)),
    ("remember", re.compile(r"^(?:please )?(?:remember|note|keep in mind) (?:that )?(.+?)[.!]?$", re.I)),
    ("what_remember", re.compile(
        r"^what (?:do you|have you|did you) (?:remember|know|remembered|learned|learn)(?: about me)?\??$"
        r"|^what'?s in your memory\??$|^show (?:me )?(?:your )?memor(?:y|ies)[.!]?$", re.I)),
    ("stop_media", re.compile(
        r"^(?:please )?(?:stop|pause|freeze|silence|kill)(?: (?:the|this|that))? (?:song|music|video|playback|track|youtube)(?: (?:where|which) (?:it is|it's) playing)?[.!]?$"
        r"|^(?:please )?(?:pause (?:the )?playback|pause the video|pause the song|pause the music|stop playback|pause|stop playing|why is it (?:still )?playing(?: still)?)[.!?]?$", re.I)),
    ("resume_media", re.compile(r"^(?:please )?(?:resume|unpause|continue)(?: (?:the|this|that))? (?:song|music|video|playback|track)?[.!]?$", re.I)),
    ("close_window", re.compile(
        r"^(?:please |can you |could you )*(?:close|kill|shut down|exit|quit)(?: (?:the|all))?(?: (?:active|current))?"
        r"(?: (?:window|tab|browser|google chrome|chrome|youtube|videos?|video|vs code|vscode|editor|notepad)(?: (?:and|,)? (?:the )?(?:videos?|tabs?|google chrome|chrome))*)?"
        r"(?: (?:and|,)? (?:stop|pause)(?: (?:playing|playback))?)?"
        r"(?: (?:you|it|we) played)?[.!]?$", re.I
    )),
    ("youtube", re.compile(
        r"^(?:(?:go (?:and|to) )?(?:search|look up|find|play)(?: on)? youtube (?:and |to )?(?:play )?(?:the )?(?:music |song )?)(.+?)[.!?]?$"
        r"|^(?:search|look up|find|play|listen to) (?:the )?(?:music |song )?(.+?) on youtube[.!?]?$"
        r"|^youtube (?:play |search )?(?:the )?(?:music |song )?(.+?)[.!?]?$", re.I)),
    ("search", re.compile(
        r"^(?:search|google|look up)(?: the web| google| online| the internet)?(?: for)? (.+?)[.!?]?$", re.I)),
    ("facebook", re.compile(
        r"^(?:(?:can you |could you |please )*(?:go to |go |open )?(?:facebook|fb|messenger)(?: and |, )?(?:open messenger|search for|search|find|look for)?(?: for)?\s*(.+?)[.!?]?)$",
        re.I
    )),
    ("write_code", re.compile(
        r"^(?:(?:can you |could you |please |would you )*(?:open (?:vs code|vscode|the editor) (?:and |to )?)?)*"
        r"(?:write|create|start writing|make|generate|type|code)(?: (?:a|an|some))? "
        r"(?:basic |sample |new |original )?(python|matlab|c\+\+|c|javascript|web)?\s*"
        r"(?:script|code|program|file)?"
        r"(?: (?:in|into|for|using) (?:vs code|vscode|the editor))?"
        r"(?: (?:about|for|to|like) (.+?))?[.!?]?$",
        re.I
    )),
    ("type_in", re.compile(r"^(?:write|type) (.+?) (?:in|into) (notepad|word)[.!]?$", re.I)),
    ("private_on", re.compile(r"^(?:private mode(?: on)?|go private|enter private mode|stop recording|don'?t record)[.!]?$", re.I)),
    ("private_off", re.compile(r"^(?:private mode off|leave private mode|exit private mode|you can look again|end private mode)[.!]?$", re.I)),
    ("autostart_on", re.compile(r"^(?:(?:please )?start (?:with|on) windows(?: (?:start ?up|login|boot))?|enable auto ?start|auto ?start on|(?:launch|run|start) (?:yourself )?(?:at|on) (?:login|start ?up|boot)|always (?:be )?(?:on|running))[.!]?$", re.I)),
    ("autostart_off", re.compile(r"^(?:(?:don'?t|do not|stop) (?:start(?:ing)?|launch(?:ing)?|run(?:ning)?) (?:with|on|at) (?:windows|login|start ?up|boot)|disable auto ?start|auto ?start off)[.!]?$", re.I)),
    ("open_ide", re.compile(
        r"^(?:please )?open (vs code|vscode|code|visual studio code|matlab) in (?:my |the )?(.+?)(?: folder| project)?[.!]?$"
        r"|^(?:please )?open (?:my |the )?(.+?) (?:folder|project) in (vs code|vscode|code|visual studio code|matlab)[.!]?$", re.I)),
    ("open_project", re.compile(r"^(?:please )?open (?:my |the )?(.+?) project[.!]?$|^(?:please )?open project (.+?)[.!]?$", re.I)),
    ("open", re.compile(r"^(?:please )?(?:open|launch|start|run) (?:up )?(.+?)(?: for me| please)?[.!]?$", re.I)),
    ("observe_on", re.compile(
        r"^(?:turn on|enable|start|allow) (?:screen )?(?:observation|observing|watching|screen capture|seeing|vision)[.!]?$"
        r"|^(?:watch|see|look at) my screen(?: from now on)?[.!]?$", re.I)),
    ("observe_off", re.compile(
        r"^(?:turn off|disable|stop) (?:screen )?(?:observation|observing|watching|screen capture|seeing|vision)[.!]?$"
        r"|^(?:stop|don'?t) (?:watching|looking at|observing) my screen[.!]?$", re.I)),
    ("stop_speech", re.compile(
        r"^(?:please )?(?:eli,? )?(?:stop|stop talking|stop speaking|be quiet|shut up|quiet|silence|hush|stop eli|eli stop|ellie stop|stop ellie)[.!]?$", re.I
    )),
    ("stop_all", re.compile(
        r"^(?:please )?(?:stop(?: the)? (?:task|working|all tasks)|"
        r"stop whatever (?:you are|it is) (?:working(?: on)?|doing)|"
        r"cancel(?: the)?(?: task| everything)?|abort|halt)[.!]?$", re.I
    )),
    ("write_code_compound", re.compile(
        r"^(?:(?:can you |could you |please |would you )*(?:open (?:the )?(?:vs code|vscode|the editor|editor) (?:and |to )?)?)*"
        r"(?:write|create|start writing|make|generate|type|code)(?: and open)?(?: (?:the|a|an|some))?\s*"
        r"(?:new |sample |basic |original )?(python|matlab|c\+\+|c|javascript|web)?\s*"
        r"(?:script|code|program|file)?\s*"
        r"(?:and (?:write|create|make|type|generate) (?:a |some )?(?:new )?(?:code|script|program|file))?\s*"
        r"(?:.*?(error|errors|bug|bugs|problem|broken|syntax error|with error|with errors))?[.!?]?$",
        re.I
    )),
    ("follow_on", re.compile(r"^(?:follow me|follow my (?:cursor|mouse)|come with me|stay with me|follow mode(?: on)?|start following(?: me)?)[.!]?$", re.I)),
    ("follow_off", re.compile(r"^(?:stay here|stay there|stay put|stop following(?: me)?|don'?t follow me|follow mode off)[.!]?$", re.I)),
    ("trust_on", re.compile(
        r"^(?:trust mode(?: on)?|auto[- ]?approve(?: everything| on| all)?|just do it|(?:you can )?(?:act|do things|do it) without asking(?: me)?|"
        r"stop asking(?: me)?(?: for (?:confirmation|permission))?|approve everything|no confirmations?)[.!]?$", re.I)),
    ("trust_off", re.compile(
        r"^(?:trust mode off|stop auto[- ]?approving|auto[- ]?approve off|ask (?:me )?(?:before|for) (?:acting|doing things|confirmation|permission)|"
        r"ask me first)[.!]?$", re.I)),
    ("remind", re.compile(r"^(?:please )?(?:eli,? )?remind me (.+)$", re.I)),
    ("job_every", re.compile(r"^(?:please )?(?:eli,? )?(every (?:\d+ |an? |half an )?(?:seconds?|minutes?|mins?|hours?|days?|weeks?|morning|evening|day|hour|week)\b.+)$", re.I)),
    ("job_in", re.compile(r"^(?:please )?(?:eli,? )?(in (?:\d+|an?|half an) (?:seconds?|minutes?|hours?|days?),? .+)$", re.I)),
    ("job_watch", re.compile(r"^(?:please )?(?:eli,? )?(?:keep (?:checking|watching|monitoring|an eye on)|watch|monitor|keep track of) (.+)$", re.I)),
    ("list_jobs", re.compile(r"^(?:what are you (?:working on|doing|tracking)|(?:list|show) (?:my |your |the )?(?:tasks|jobs|reminders|schedule|scheduled (?:tasks|jobs))|what(?:'s| is) scheduled|what do you have scheduled|any reminders)[?.!]?$", re.I)),
    ("cancel_job", re.compile(r"^(?:cancel|stop|delete|remove|forget) (?:the |my |your )?(?:task|job|reminder|schedule)(?: number| #)?\s*(\d+)[.!]?$", re.I)),
    ("cancel_jobs", re.compile(r"^(?:cancel|stop|delete|remove|clear) (?:all )?(?:my |your |the )?(?:reminders|tasks|jobs|schedule|scheduled (?:tasks|jobs))[.!]?$", re.I)),
    ("guide_start", re.compile(r"^(?:eli,? )?(?:guide me(?: through)?|walk me through|show me how to|teach me(?: how to)?|help me)\s+(.+)$", re.I)),
    ("guide_next", re.compile(r"^(?:next|next step|done|i did it|i'?ve done it|finished|completed|step done|that'?s done)[.!]?$", re.I)),
    ("guide_repeat", re.compile(r"^(?:repeat|again|say (?:that|it) again|repeat (?:that|the step)|what was that)[.!?]?$", re.I)),
    ("guide_back", re.compile(r"^(?:back|go back|previous step|last step)[.!]?$", re.I)),
    ("guide_skip", re.compile(r"^(?:skip|skip (?:this|that|the) step|skip it)[.!]?$", re.I)),
    ("guide_alt", re.compile(r"^(?:another way|alternative|is there another way|i'?m stuck|help|show me another way)[.!?]?$", re.I)),
    ("guide_stop", re.compile(r"^(?:stop|end|exit|cancel|quit) (?:the )?(?:guide|guiding|walkthrough|tutorial)[.!]?$", re.I)),
    ("approve", re.compile(r"^(?:approve(?: it)?|yes(?:,? (?:do it|go ahead|please))?|go ahead|do it|confirm|allow(?: it)?|ok(?:ay)?(?:,? (?:go ahead|do it))?|sure)[.!]?$", re.I)),
    ("deny", re.compile(r"^(?:cancel(?: that| it)?|no(?:,? (?:don'?t|cancel|stop))?|deny|don'?t(?: do it)?|stop that|never ?mind|abort)[.!]?$", re.I)),
    ("autofix_on", re.compile(r"^(?:fix (?:things|errors|issues) automatically|auto[- ]?fix(?: on)?|proactive mode(?: on)?)[.!]?$", re.I)),
    ("autofix_off", re.compile(r"^(?:ask before fixing|auto[- ]?fix off|proactive mode off|just ask me first about fixes)[.!]?$", re.I)),
    ("learn_3d", re.compile(
        r"^(?:tell me |show me |what are )?(?:the )?steps to learn 3d model(?:l)?ing(?: from scratch)?(?: without api key)?[.!?]?$"
        r"|^(?:how (?:do i|to) learn 3d model(?:l)?ing|learn 3d model(?:l)?ing from scratch)[.!?]?$", re.I)),
    ("greeting", re.compile(r"^(?:hello|hey|hi|howdy|good morning|good afternoon|good evening|yo)(?: there)?[.!]?$", re.I)),
    ("vscode_check_code", re.compile(
        r"^(?:(?:can you |could you |please )*(?:go to|open|switch to|look at) (?:vs code|vscode|the editor|visual studio code)(?: and |, )?)?"
        r"(?:check|inspect|analyse|analyze|see|look at|review|find)(?: (?:the|my))? (?:code|codes|script|file|errors?)"
        r"(?: (?:i (?:have |'ve )?written|i wrote|in (?:vs code|vscode|the editor|here)))?"
        r"(?:.*?(?:error|errors|bug|bugs|problem|wrong))?[.!?]?$",
        re.I
    )),
    ("check_errors", re.compile(
        r"^(?:auto )?(?:search|check|find|scan)(?: for)? errors in (?:my )?([a-zA-Z0-9_#+ -]+?) (?:code|codes|project|files?)(?: offline)?[.!?]?$"
        r"|^(?:check|scan) (?:my )?([a-zA-Z0-9_#+ -]+?) (?:code|codes|files?) for errors(?: offline)?[.!?]?$", re.I)),
]

SCREEN_RE = re.compile(
    r"(\bscreen\b|\bsee\b|\bseeing\b|looking at|look at|what am i|what'?s (?:this|that|wrong|on|happening)|"
    r"\bwrong\b|failing|\bfix\b|\berror\b|\bbug\b|traceback|crash|explain (?:this|the|it)|"
    r"this (?:code|file|page|window|design|error|part|model|screenshot|thing)|\bdesign\b|\bcad\b|sketch|"
    r"what is this|help me (?:understand|with) this|read (?:this|the|what)|why (?:is|does|did) (?:this|it|that)|"
    r"check my (?:design|model|part|enclosure|code)|review (?:my|this))",
    re.I,
)
ERROR_RE = re.compile(r"(wrong|failing|fail|fix|error|bug|traceback|crash|broken|not working|exception)", re.I)
PREF_RE = re.compile(
    r"\b(i (?:usually|always|prefer|like|love|hate|never|often|mostly|only) |my favou?rite|i'?m a |i am a |call me |"
    r"my name is|i work (?:at|on|with)|i use )", re.I)


def extract_persona_switch(text: str) -> Optional[tuple[str, list[str]]]:
    """Detect requests to switch active Eli avatar persona, character, or voice."""
    t = text.lower().strip().strip(".!?,")
    from .personas import find_persona_by_query
    
    is_switch_phrase = (
        re.search(r"\b(switch|change|set|turn|become|use|choose|call|see|talk to|speak to|bring up|give me|connect me to|show me|i want|wake up)\b", t) is not None or
        re.search(r"\b(el+i+e?|ally|allie)\s+(for|to|as)\b", t) is not None or
        re.search(r"\b(version\s+of\s+(el+i+e?|ally|allie))\b", t) is not None or
        re.search(r"\b(masculine|feminine|male|female)\b", t) is not None
    )
    
    matched = find_persona_by_query(t)
    if matched and is_switch_phrase:
        return "switch_persona", [matched.id]
    
    if t in ("atlas", "aria", "zephyr", "mentor", "classic", "eli", "ellie", "hotel eli", "agency eli", "event eli", "teach me eli", "masculine", "feminine", "ira", "eira", "zefir", "defire"):
        if matched:
            return "switch_persona", [matched.id]
            
    return None


def extract_vscode_code_request(text: str) -> Optional[tuple[str, list[str]]]:
    t = text.lower().strip().strip(".!?,")
    has_vscode = any(k in t for k in ("vs code", "vscode", "visual studio code", "js code", "the editor", "code in my laptop"))
    has_code_action = any(k in t for k in (
        "write a code", "write code", "writing a code", "writing code",
        "create code", "start writing", "code in there", "start coding",
        "make a code", "write a", "writing a", "open js code in my laptop and write a code"
    ))
    has_refresh = any(k in t for k in ("refresh me", "refresh", "relaxing", "relax", "calm"))

    if not (has_vscode and (has_code_action or has_refresh)):
        return None

    topic = "refresh me" if has_refresh else "sample code"
    lang = "python"
    auto_trust = any(k in t for k in ("trust", "accept", "ok button", "allow", "yes", "permission", "question like do you trust"))
    return "write_code_vscode", [topic, lang, str(auto_trust)]


def extract_close_request(text: str) -> Optional[tuple[str, list[str]]]:
    t = text.lower().strip().strip(".!?,")
    if not any(k in t for k in ("close", "shut down", "kill", "exit", "quit")):
        return None

    # Exclude queries asking why something was closed or negative directives
    if any(k in t for k in ("why did you close", "don't close", "dont close", "not to close", "without closing")):
        return None

    # 1. Everything / All windows / All tabs
    if any(k in t for k in ("everything", "all the tabs", "all tabs", "all windows", "active windows are not closed")):
        if "chrome" in t:
            return "close_window", ["Google Chrome"]
        elif "edge" in t:
            return "close_window", ["Microsoft Edge"]
        return "close_window", ["everything"]

    # 2. Specific browser / app targets
    if "chrome" in t or "google chrome" in t:
        return "close_window", ["Google Chrome"]

    if "edge" in t or "microsoft edge" in t:
        return "close_window", ["Microsoft Edge"]

    if "vs code" in t or "vscode" in t or "the editor" in t:
        return "close_window", ["Visual Studio Code"]

    if "notepad" in t:
        return "close_window", ["Notepad"]

    # 3. Tabs vs Windows
    if re.search(r"\b(?:all\s+)?tabs?\b", t):
        return "close_window", ["tab"]

    if re.search(r"\b(?:browsers?|google|chrome|edge)\b", t):
        return "close_window", ["browser"]

    if re.search(r"\b(?:windows?|active\s+window|current\s+window)\b", t):
        return "close_window", ["window"]

    # 4. Conversational close (e.g. "well, listen, close here", "close please", "just close")
    if re.search(r"\b(?:close|shut down|exit|quit)\b", t):
        return "close_window", ["window"]

    return None


def extract_youtube_request(text: str) -> Optional[dict]:
    t = text.lower().strip().strip(".!?,")
    if any(k in t for k in (
        "you lie", "you lied", "didn't", "didnt", "why did you", "why is it", "confident you lie",
        "doesn't", "doesnt", "close", "stop", "pause", "kill", "exit", "quit", "submit", "antigravity",
        "never", "clicks", "talking", "recording", "unhinged", "wasnt supposed", "take your time", "sorry",
        "nothing to be sorry", "guess it is still", "fluctuated", "what is the question"
    )):
        return None
    if len(t.split()) > 18:
        return None
    if not any(k in t for k in ("youtube", "song", "music", "video", "play", "track")):
        return None

    want_chrome = any(k in t for k in ("chrome", "google chrome", "browser"))

    # Step 1: Strip leading browser launch commands (e.g. "can you please go to Google Chrome and", "go open Chrome then")
    clean = re.sub(
        r"^(?:(?:can you |could you |please |would you |i ask (?:you |it )?to |ask (?:you |it )?to )*(?:go(?:\s+(?:and|to|open))?|open|launch|navigate(?:\s+to)?|switch\s+to)\s*(?:open )?\s*(?:google\s+)?(?:chrome|browser|the browser|edge)(?: and |, | then )*)+",
        "",
        t,
        flags=re.I
    ).strip()

    # Step 2: Strip leading youtube navigation commands (e.g. "search for YouTube, then", "go to YouTube and")
    clean = re.sub(
        r"^(?:(?:then |and )?(?:search(?:\s+(?:for|on))?|look up|go(?:\s+to)?|open|navigate(?:\s+to)?)\s+(?:for |on )?youtube(?: and |, | then )*)+",
        "",
        clean,
        flags=re.I
    ).strip()

    # Step 3: Strip leading action verbs ("then play the music, ", "play a video of", etc.)
    clean = re.sub(r"^(?:then |and |to )+", "", clean, flags=re.I).strip()
    clean = re.sub(
        r"^(?:search(?:\s+for)?|play(?:\s+the)?|listen\s+to|find)\s+(?:a |the |some |any )?(?:music|songs?|tracks?|videos?)?\b(?:called |of )?[\s,]*",
        "",
        clean,
        flags=re.I
    ).strip()

    # Step 4: Strip trailing boilerplate ("and play a video of among them", "on youtube", etc.)
    clean = re.sub(r"(?:\s+(?:and|then|to))?\s*(?:play|watch)(?:\s+(?:a |the )?(?:video|song|track))?(?:\s+(?:of among them|among them|of them|of it|it))?[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"(?:\s+(?:on youtube|in chrome|in google chrome|on google|on the web|in browser))+[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"\s+(?:and|then|to|please)$", "", clean, flags=re.I).strip()
    clean = re.sub(r"^(?:a |the |some |any )?(?:music|songs?|tracks?|videos?)\b[\s,]*", "", clean, flags=re.I).strip()
    clean = clean.strip(" ,.-'\"")

    # Step 5: Check for generic music requests ("youtube music", "any music", "some music", "music", "song")
    if not clean or clean.lower() in ("youtube", "youtube music", "google", "chrome", "music", "song", "songs", "any music", "some music", "a music", "enemy music", "the music", "a song", "tracks"):
        return {"query": "", "generic": True, "open_chrome": want_chrome or True}

    # Step 6: Strict length & sanity validation
    words = clean.split()
    if len(words) > 7 or any(w in clean for w in ("never", "starts", "talking", "recording", "clicks", "unhinged", "supposed", "submit", "close")):
        return None

    return {"query": clean, "generic": False, "open_chrome": want_chrome or True}


def extract_browser_search_request(text: str) -> Optional[dict]:
    t = text.lower().strip().strip(".!?,")
    if extract_vscode_code_request(t) is not None:
        return None
    if any(k in t for k in (
        "youtube", "song", "music", "video",
        "close", "shut", "kill", "stop", "pause", "exit", "quit", "cancel",
        "write a code", "write code", "writing a code",
        "vs code", "vscode", "js code", "on your phone"
    )):
        return None

    if not any(k in t for k in ("search", "google", "look up", "find", "open chatgpt", "go to chatgpt", "chrome", "edge")):
        return None

    # Multi-step conjunctions are task instructions, not web searches
    if any(c in t for c in (" and go ", " and start ", " and write ", " there will be ", " you have to ", " you had to ")):
        return None

    want_edge = any(k in t for k in ("microsoft edge", "edge browser", "edge"))
    browser = "edge" if want_edge else "chrome"

    # Direct "go to / open chatgpt"
    if re.search(r"\b(?:go\s+to|open|launch)\s+chatgpt\b", t):
        return {"query": "ChatGPT", "engine": "google", "browser": browser, "open_chrome": browser == "chrome", "open_edge": browser == "edge"}

    # Compound browser + search
    clean = re.sub(
        r"^(?:(?:can you |could you |please )*(?:go(?:\s+(?:and|to))?\s+|open\s+|switch\s+to\s+)?(?:the\s+)?(?:google\s+)?(?:chrome|browser|the browser|microsoft\s+edge|edge|google)(?: and |, | then )*)+",
        "",
        t,
        flags=re.I
    ).strip()

    clean = re.sub(r"^(?:then |and )+", "", clean, flags=re.I).strip()
    clean = re.sub(
        r"^(?:search|google|look up|find)(?:\s+(?:the\s+web|google|online|the\s+internet))?(?:\s+for|\s+on|\s+in)?\s*",
        "",
        clean,
        flags=re.I
    ).strip()

    # Strip trailing browser directives (e.g. ". Go to Microsoft Edge browser", "in edge", "on edge")
    clean = re.sub(r"(?:[\.,;]?\s*(?:go to|open|in|using|on|with)?\s*(?:the\s+)?(?:microsoft\s+)?edge(?:\s+browser)?)+[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"(?:[\.,;]?\s*(?:go to|open|in|using|on|with)?\s*(?:the\s+)?(?:google\s+)?chrome(?:\s+browser)?)+[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"\s+(?:on\s+google\s+chrome|in\s+google\s+chrome|on\s+chrome|in\s+chrome|on\s+google|in\s+browser|in\s+the\s+browser)[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"\s+(?:and|then|to|please)$", "", clean, flags=re.I).strip()

    # Capitalize ChatGPT if query is chatgpt
    if clean.lower() == "chatgpt":
        clean = "ChatGPT"

    # Reject if query is too long or contains desktop app targets
    if len(clean.split()) > 8 or any(k in clean.lower() for k in ("vs code", "vscode", "js code", "in my laptop", "write a code", "write code")):
        return None

    if clean and clean not in ("chrome", "google chrome", "browser", "google", "the web", "online", "internet", "edge", "microsoft edge"):
        return {"query": clean, "engine": "google", "browser": browser, "open_chrome": browser == "chrome", "open_edge": browser == "edge"}
    return None



def extract_code_check_request(text: str) -> Optional[tuple[str, list[str]]]:
    t = text.lower().strip().strip(".!?,")
    # Negative filters - only reject external web/media/window operations
    if any(k in t for k in ("google", "youtube", "weather", "news", "music", "facebook", "messenger", "close", "shut down", "exit", "quit")):
        return None
    if any(k in t for k in ("why did you", "why is it", "you lied", "how do i", "how to")):
        return None

    # Extract target python script if specified (e.g. A.py, test.py, my_script.py)
    fn_match = re.search(r"\b([a-zA-Z0-9_\-]+\.py)\b", text, re.I)
    target_fn = fn_match.group(1) if fn_match else ""

    # Explicit phrases asking to check/inspect/read code or check how it is
    if any(k in t for k in (
        "read the whole script", "read the script", "read this script", "read whole script",
        "check the script", "check my script", "check this script",
        "check the code", "check my code", "check this code", "check code",
        "check errors", "check for errors", "find errors", "diagnose code", "diagnose script",
        "identify the errors", "specifically identify the errors", "any errors in the code",
        "inspect the code", "inspect my code", "inspect the script", "inspect my script",
        "check how it is", "check how is", "see how it is", "how it is"
    )):
        return "vscode_check_code", [target_fn] if target_fn else []

    if target_fn and any(k in t for k in ("how", "check", "inspect", "errors", "look", "see", "run", "status", "search", "find")):
        return "vscode_check_code", [target_fn]

    # Combinations of (check / inspect / read / diagnose / review / search / find) + (code / script / .py)
    has_action = bool(re.search(r"\b(check|inspect|read|diagnose|review|find|see|look at|search)\b", t))
    has_target = bool(re.search(r"\b(code|codes|script|scripts|\.py)\b", t))
    has_vs = bool(re.search(r"\b(vs code|vscode|editor)\b", t))
    has_err = bool(re.search(r"\b(errors?|bugs?|problems?|wrong|mistakes?)\b", t))

    if (has_action and has_target) or (has_action and has_err and (has_target or has_vs)) or (has_target and has_err) or (has_vs and has_action):
        return "vscode_check_code", [target_fn] if target_fn else []

    return None


TEACH_START_RE = re.compile(
    r"^(?:eli[, ]+)?(?:(?:let me|i(?:'ll| will| want to| can))\s+)?(?:teach|show) you (?:how (?:to|i) |to )?(.+?)[.!]?$"
    r"|^(?:eli[, ]+)?(?:watch me|watch how i|learn (?:this|how i do this|from me)(?::| -)?|record (?:this|me|how i)(?: doing)?)\s*(.*?)[.!]?$"
    r"|^(?:eli[, ]+)?(?:start|begin) (?:a )?(?:recording|teach(?:ing)? mode|learning)(?: (?:for|of|called|named))?\s*(.*?)[.!]?$",
    re.I,
)
TEACH_STOP_RE = re.compile(
    r"^(?:eli[, ]+)?(?:stop|end|finish|done|that'?s it|i'?m done)(?: (?:the |with the )?(?:recording|teaching|lesson|demo|demonstration))?(?:,? (?:that'?s (?:it|all)|i'?m done))?[.!]?$"
    r"|^(?:eli[, ]+)?(?:stop recording|recording done|that'?s the whole thing|i'?m finished)[.!]?$", re.I)
TEACH_CANCEL_RE = re.compile(r"^(?:eli[, ]+)?(?:cancel|scrap|forget|discard) (?:the |this )?(?:recording|lesson|demo)[.!]?$", re.I)
TEACH_LIST_RE = re.compile(r"^(?:eli[, ]+)?(?:what|which) (?:guides|walkthroughs|lessons) (?:do you (?:have|know)|have you learned)\??$"
                           r"|^(?:eli[, ]+)?(?:list|show) (?:me )?(?:my|your|the) (?:learned )?(?:guides|walkthroughs)[.!?]?$", re.I)
TEACH_REVIEW_RE = re.compile(r"^(?:eli[, ]+)?(?:review|check|show|read (?:me|back)) (?:that|the|this|last|my) guide(?: (?:for|called|named) (.+?))?[.!?]?$", re.I)
TEACH_EXPORT_RE = re.compile(r"^(?:eli[, ]+)?(?:export|share|save) (?:that|the|this|last|my) guide(?: (?:for|called|named) (.+?))?(?: (?:to|as) (?:a )?file)?[.!?]?$", re.I)


def extract_teach_request(text: str):
    """Teach-me intents. Only 'stop recording'-style phrases are matched while a recording is active
    (main_agent checks the recorder state), so an ordinary 'stop' never ends up here."""
    t = (text or "").strip()
    m = TEACH_CANCEL_RE.match(t)
    if m:
        return "teach_cancel", []
    m = TEACH_START_RE.match(t)
    if m:
        name = next((g for g in m.groups() if g), "").strip()
        name = re.sub(r"^(?:how to|to)\s+", "", name, flags=re.I).strip()
        if not re.search(r"\b(?:stop|cancel)\b", name, re.I):
            return "teach_start", [name]  # empty name -> Eli asks what to call the guide
    if TEACH_STOP_RE.match(t) and re.search(r"record|teach|lesson|demo|finished|whole thing", t, re.I):
        return "teach_stop", []
    if TEACH_LIST_RE.match(t):
        return "teach_list", []
    m = TEACH_REVIEW_RE.match(t)
    if m:
        return "teach_review", [(m.group(1) or "").strip()]
    m = TEACH_EXPORT_RE.match(t)
    if m:
        return "teach_export", [(m.group(1) or "").strip()]
    return None


def match(text: str):

    from .speech.stt import normalize_speech_text
    clean_text = normalize_speech_text(text)
    t = strip_wake(clean_text).strip()
    if not t:
        if re.search(r"\b(hello|hey|hi|good morning|good evening|howdy|yo|[iea]+l+[ieya]+|allie|ali|ally)\b", clean_text, re.I):
            return "greeting", []
        return None

    # 1. Stop/Cancel commands have immediate priority
    low = t.lower()
    if low in ("stop", "stop the task", "stop task", "stop working", "stop it", "stop that", "stop immediately", "cancel", "cancel the task", "cancel task", "abort", "halt"):
        return "stop_all", []
    if any(k in low for k in ("stop the task", "stop whatever it is working", "stop whatever you are working", "stop working immediately", "cancel the task")):
        return "stop_all", []

    # 2. Teach-me mode (record once -> guide), before generic patterns so "show you how to X" isn't a screen request
    teach_req = extract_teach_request(t)
    if teach_req:
        return teach_req

    # 3. Persona / Avatar switching (masculine, feminine, agency, hotel, event, mentor)
    persona_req = extract_persona_switch(t) or extract_persona_switch(clean_text)
    if persona_req:
        return persona_req

    # 4. Direct priority matching for Close Window / Tab / Browser (catches "close all the tabs here, close Google Chrome", etc.)
    close_req = extract_close_request(t)
    if close_req:
        return close_req

    for kind, rx in PATTERNS:
        if kind in ("close_window", "stop_media", "stop_speech"):
            m = rx.match(t)
            if m:
                groups = [g.strip() for g in m.groups() if g]
                return kind, groups

    # 5. Compound or natural YouTube request (strictly validated before generic PATTERNS)
    yt_req = extract_youtube_request(t)
    if yt_req:
        if yt_req.get("generic"):
            return "youtube_ask_song", []
        elif yt_req.get("query"):
            return "youtube", [yt_req["query"]]

    # 5b. Dedicated VS Code code creation request (strictly prioritized before browser search)
    vscode_req = extract_vscode_code_request(t)
    if vscode_req:
        return vscode_req

    # 5c. Dedicated Code check & error inspection request
    code_check_req = extract_code_check_request(t)
    if code_check_req:
        return code_check_req


    # 6. Compound or natural browser search request (with Edge and Chrome support)
    search_req = extract_browser_search_request(t)
    if search_req and search_req.get("query"):
        if search_req.get("browser") == "edge":
            return "browser_search_edge", [search_req["query"]]
        return "browser_search", [search_req["query"]]

    # 6. Direct pattern matching
    for kind, rx in PATTERNS:
        m = rx.match(t)
        if m:
            groups = [g.strip() for g in m.groups() if g]
            if kind == "open" and groups:
                arg = groups[0].lower()
                if any(delim in arg for delim in (",", ";", " and ", " then ", " click", " type")):
                    continue
            return kind, groups

    # Robust keyword fallbacks for crucial instructions
    low = t.lower()
    if low in ("stop", "stop the task", "stop task", "stop working", "stop it", "stop that", "stop immediately", "cancel", "cancel the task", "cancel task", "abort", "halt"):
        return "stop_all", []
    if any(k in low for k in ("stop the task", "stop whatever it is working", "stop whatever you are working", "stop working immediately", "cancel the task")):
        return "stop_all", []

    if any(k in low for k in ("stop", "pause", "freeze", "silence", "kill")) and any(k in low for k in ("song", "music", "video", "playback", "youtube", "playing")):
        return "stop_media", []
    if "why is it" in low and "playing" in low:
        return "stop_media", []

    if any(k in low for k in ("resume", "unpause", "continue")) and any(k in low for k in ("song", "music", "video", "playback", "youtube", "playing")):
        return "resume_media", []

    if any(k in low for k in ("3d model", "3d design", "learn 3d", "learn blender", "steps to learn 3d")):
        return "learn_3d", []

    if any(k in low for k in ("vs code", "vscode", "in here", "the code i have written", "code i wrote", "editor")) and any(k in low for k in ("check", "inspect", "analyse", "analyze", "see", "error", "errors", "look")):
        return "vscode_check_code", []

    if any(k in low for k in ("error", "errors", "syntax")) and any(k in low for k in ("code", "matlab", "python", "c++", "c ", "project")):
        target = "matlab" if "matlab" in low else "python" if "python" in low else "c++" if "c++" in low else "c" if "c " in low else ""
        return "check_errors", [target] if target else []

    if low in ("hello", "hey", "hi", "howdy", "good morning", "good afternoon", "good evening"):
        return "greeting", []

    return None


def wants_screen(text: str) -> bool:
    return bool(SCREEN_RE.search(text))


def wants_error_help(text: str) -> bool:
    return bool(ERROR_RE.search(text))


def classify_kind(statement: str) -> str:
    return "preference" if PREF_RE.search(statement) else "fact"


_FLIPS = [
    (re.compile(r"\bi am\b", re.I), "you are"), (re.compile(r"\bi'm\b", re.I), "you're"),
    (re.compile(r"\bi've\b", re.I), "you've"), (re.compile(r"\bi'll\b", re.I), "you'll"),
    (re.compile(r"\bmy\b", re.I), "your"), (re.compile(r"\bmine\b", re.I), "yours"),
    (re.compile(r"\bmyself\b", re.I), "yourself"), (re.compile(r"\bme\b", re.I), "you"),
    (re.compile(r"\bi\b"), "you"),
]


def second_person(statement: str) -> str:
    """'my favorite language is Python' -> 'your favorite language is Python'"""
    s = statement
    for rx, rep in _FLIPS:
        s = rx.sub(rep, s)
    return s


def third_person(statement: str) -> str:
    """Stored form: 'User's favorite language is Python.'"""
    s = second_person(statement)
    s = re.sub(r"\byour\b", "the user's", s, flags=re.I)
    s = re.sub(r"\byou are\b", "the user is", s, flags=re.I)
    s = re.sub(r"\byou're\b", "the user is", s, flags=re.I)
    s = re.sub(r"\byou\b", "the user", s, flags=re.I)
    s = s[0].upper() + s[1:] if s else s
    return s.rstrip(".") + "."
