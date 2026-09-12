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
    ("skip_ad", re.compile(r"^(?:please )?(?:skip (?:the )?(?:ad|ads|video ad|and)|skip it|skip)[.!]?$", re.I)),
    ("stop_media", re.compile(
        r"^(?:please )?(?:stop|pause|freeze|silence|kill)(?: (?:the|this|that))? (?:song|music|video|playback|track|youtube)(?: (?:where|which) (?:it is|it's) playing)?[.!]?$"
        r"|^(?:please )?(?:pause (?:the )?playback|pause the video|pause the song|pause the music|stop playback|pause|stop playing|why is it (?:still )?playing(?: still)?)[.!?]?$", re.I)),
    ("resume_media", re.compile(r"^(?:please )?(?:resume|unpause|continue)(?: (?:the|this|that))? (?:song|music|video|playback|track)?[.!]?$", re.I)),
    ("close_window", re.compile(
        r"^(?:please |can you |could you )*(?:close|kill|shut down|exit|quit)(?: (?:the|all))?(?: (?:active|current))?"
        r"(?: (?:window|tab|browser|google chrome|chrome|youtube|videos?|video|vs code|vscode|editor|notepad)(?: (?:and|,)? (?:the )?(?:videos?|tabs?|google chrome|chrome))*)?"
        r"(?: (?:you|it|we) played)?[.!]?$", re.I
    )),
    ("click_allow", re.compile(r"^(?:please )?(?:click|press) (?:allow|submit|proceed|yes|confirm|approve)(?: (?:on|for) (?:antigravity|prompt|screen|dialog))?[.!]?$", re.I)),
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
    ("auto_allow_on", re.compile(
        r"^(?:(?:always )?(?:click|auto[- ]?click) (?:allow|submit|proceed)(?: (?:everytime|every time|when|whenever) antigravity asks)?|"
        r"auto[- ]?allow antigravity(?: on)?|click allow and submit(?: while i(?:'m| am) away)?)[.!]?$", re.I)),
    ("auto_allow_off", re.compile(
        r"^(?:stop (?:auto[- ]?clicking|clicking) allow|auto[- ]?allow (?:antigravity )?off|don'?t auto[- ]?click allow)[.!]?$", re.I)),
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


def is_user_complaint_not_playing(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in (
        "you lie", "you lied", "confident you lie",
        "didn't even open", "didnt even open",
        "didn't open", "didnt open",
        "never clicks", "never opens", "never clicked", "never opened",
        "starts talking again", "recording its own", "unhinged", "wasnt supposed to be like this",
        "wasn't supposed", "fluctuated words",
        "didn't search", "didnt search",
        "didn't play", "didnt play",
        "nothing is playing", "chrome isn't open", "chrome is not open",
        "didn't go to", "didnt go to"
    ))


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
        r"^(?:search(?:\s+for)?|play(?:\s+the)?|listen\s+to|find)\s+(?:a |the )?(?:music|song|track|video)?(?:called |of )?[\s,]*",
        "",
        clean,
        flags=re.I
    ).strip()

    # Step 4: Strip trailing boilerplate ("and play a video of among them", "on youtube", etc.)
    clean = re.sub(r"(?:\s+(?:and|then|to))?\s*(?:play|watch)(?:\s+(?:a |the )?(?:video|song|track))?(?:\s+(?:of among them|among them|of them|of it|it))?[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"(?:\s+(?:on youtube|in chrome|in google chrome|on google|on the web|in browser))+[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"\s+(?:and|then|to|please)$", "", clean, flags=re.I).strip()
    clean = re.sub(r"^(?:a |the )?(?:music |song |track |video )?[\s,]*", "", clean, flags=re.I).strip()
    clean = clean.strip(" ,.-'\"")

    # Step 5: Normalize generic requests ("any music", "some music", "music", "song")
    if not clean or clean in ("youtube", "google", "chrome", "music", "song", "any music", "some music", "a music", "enemy music"):
        clean = "relaxing music"

    # Step 6: Strict length & sanity validation
    words = clean.split()
    if len(words) > 7 or any(w in clean for w in ("never", "starts", "talking", "recording", "clicks", "unhinged", "supposed", "submit", "close")):
        return None

    return {"query": clean, "open_chrome": want_chrome or True}


def extract_browser_search_request(text: str) -> Optional[dict]:
    t = text.lower().strip().strip(".!?,")
    if any(k in t for k in ("you lie", "you lied", "didn't", "didnt", "why did you", "youtube", "song", "music", "video")):
        return None

    if not any(k in t for k in ("search", "google", "look up", "find", "open chatgpt", "go to chatgpt", "chrome")):
        return None

    want_chrome = any(k in t for k in ("chrome", "google chrome", "browser"))

    # Direct "go to / open chatgpt"
    if re.search(r"\b(?:go\s+to|open|launch)\s+chatgpt\b", t):
        return {"query": "ChatGPT", "engine": "google", "open_chrome": True}

    # Compound browser + search
    clean = re.sub(
        r"^(?:(?:can you |could you |please )*(?:go\s+(?:and|to)\s+|open\s+|switch\s+to\s+)?(?:google\s+)?(?:chrome|browser|the browser|edge|google)(?: and |, | then )*)+",
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

    clean = re.sub(r"\s+(?:on\s+google\s+chrome|in\s+google\s+chrome|on\s+chrome|in\s+chrome|on\s+google|in\s+browser|in\s+the\s+browser)[.!?]?$", "", clean, flags=re.I).strip()
    clean = re.sub(r"\s+(?:and|then|to|please)$", "", clean, flags=re.I).strip()

    if clean and clean not in ("chrome", "google chrome", "browser", "google", "the web", "online", "internet"):
        return {"query": clean, "engine": "google", "open_chrome": want_chrome or True}
    return None


def match(text: str):
    from .speech.stt import normalize_speech_text
    clean_text = normalize_speech_text(text)
    t = strip_wake(clean_text).strip()
    if not t:
        if re.search(r"\b(hello|hey|hi|good morning|good evening|howdy|yo|[iea]+l+[ieya]+|allie|ali|ally)\b", clean_text, re.I):
            return "greeting", []
        return None

    low = t.lower()

    # 1. Immediate detection of user criticism about missing action or unhinged conversation
    if is_user_complaint_not_playing(t):
        return "user_complaint_not_playing", [t]

    # 2. Stop/Cancel commands have immediate priority
    if low in ("stop", "stop the task", "stop task", "stop working", "stop it", "stop that", "stop immediately", "cancel", "cancel the task", "cancel task", "abort", "halt"):
        return "stop_all", []
    if any(k in low for k in ("stop the task", "stop whatever it is working", "stop whatever you are working", "stop working immediately", "cancel the task")):
        return "stop_all", []

    # 3. Direct priority matching for Close Window / Stop Media
    for kind, rx in PATTERNS:
        if kind in ("close_window", "stop_media", "stop_speech"):
            m = rx.match(t)
            if m:
                groups = [g.strip() for g in m.groups() if g]
                return kind, groups

    # 4. Auto allow Antigravity (handles "submit everytime antigravity asks...")
    if ("allow" in low or "submit" in low) and any(k in low for k in ("antigravity", "dialog", "prompt", "everytime", "every time", "always", "auto", "whenever")):
        return "auto_allow_on", [t]
    if any(k in low for k in ("keep going", "keep continuing", "keep monitoring", "till say stop", "until i say stop", "continue monitoring", "keep doing that", "i didnt say stop", "i didn't say stop")):
        return "auto_allow_on", [t]

    # 5. Direct pattern matching
    for kind, rx in PATTERNS:
        m = rx.match(t)
        if m:
            groups = [g.strip() for g in m.groups() if g]
            if kind == "open" and groups:
                arg = groups[0].lower()
                if any(delim in arg for delim in (",", ";", " and ", " then ", " click", " type")):
                    continue
            return kind, groups

    # 6. Compound or natural YouTube request (strictly validated)
    yt_req = extract_youtube_request(t)
    if yt_req and yt_req.get("query"):
        return "youtube", [yt_req["query"]]

    # 7. Compound or natural browser search request
    search_req = extract_browser_search_request(t)
    if search_req and search_req.get("query"):
        return "browser_search", [search_req["query"]]

    # Robust keyword fallbacks for crucial instructions
    low = t.lower()
    if low in ("stop", "stop the task", "stop task", "stop working", "stop it", "stop that", "stop immediately", "cancel", "cancel the task", "cancel task", "abort", "halt"):
        return "stop_all", []
    if any(k in low for k in ("stop the task", "stop whatever it is working", "stop whatever you are working", "stop working immediately", "cancel the task")):
        return "stop_all", []

    if any(k in low for k in ("keep going", "keep continuing", "keep monitoring", "till say stop", "until i say stop", "continue monitoring", "keep doing that", "i didnt say stop", "i didn't say stop")):
        return "auto_allow_on", []
    if any(k in low for k in ("stop auto allow", "disable auto allow", "turn off auto allow", "turn off autonomous cursor", "disable autonomous cursor", "stop auto-allow")):
        return "auto_allow_off", []
    if any(k in low for k in ("start auto allow", "enable auto allow", "turn on auto allow", "turn on autonomous cursor", "enable autonomous cursor", "start auto-allow")):
        return "auto_allow_on", []
    if ("allow" in low or "submit" in low) and any(k in low for k in ("antigravity", "dialog", "prompt", "away", "everytime", "every time", "always", "auto", "whenever")):
        return "auto_allow_on", []
    if any(k in low for k in ("click allow", "allow", "click submit", "submit", "doesnt click submit", "click allow and submit", "press submit")):
        return "click_allow", []

    if "skip" in low and any(k in low for k in ("ad", "ads", "and", "video", "it")):
        return "skip_ad", []

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
