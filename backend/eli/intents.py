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
    return WAKE_PREFIX.sub("", text.strip())


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
    ("close_window", re.compile(r"^(?:please )?close (?:the )?(?:window|tab|browser|chrome|youtube|vs code|vscode|editor|notepad)[.!]?$", re.I)),
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
    ("stop_all", re.compile(
        r"^(?:please )?(?:stop(?: the)?(?: task| working| it| that| everything| immediately)?|"
        r"stop whatever (?:you are|it is) (?:working(?: on)?|doing)|"
        r"cancel(?: the)?(?: task| it| everything)?|abort|halt|stop all(?: tasks)?|"
        r"stop|be quiet|shut up|silence|stop talking)[.!]?$", re.I)),
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


def match(text: str):
    t = strip_wake(text).strip()
    if not t:
        if re.search(r"\b(hello|hey|hi|good morning|good evening|howdy|yo|[iea]+l+[ieya]+|allie|ali|ally)\b", text, re.I):
            return "greeting", []
        return None
    for kind, rx in PATTERNS:
        m = rx.match(t)
        if m:
            groups = [g.strip() for g in m.groups() if g]
            if kind == "open" and groups:
                arg = groups[0].lower()
                if any(delim in arg for delim in (",", ";", " and ", " then ", " click", " type")):
                    continue
            return kind, groups

    # Clause / multi-sentence checking: split by sentence boundaries or conjunctions
    clauses = re.split(r"[.!?;\n]+", t)
    if len(clauses) > 1 or any(c in t for c in (" and ", " then ", " like ", " also ")):
        all_parts = []
        for c in clauses:
            sub = c.strip()
            if sub:
                all_parts.append(sub)
                for sub_split in re.split(r"\b(?:also|and|like|then)\b", sub, flags=re.I):
                    s = sub_split.strip()
                    if s and s != sub:
                        all_parts.append(s)

        for part in all_parts:
            cleaned = re.sub(r"^(?:also |and |like |can you |could you |please |it should |make sure to |just )+", "", part, flags=re.I).strip()
            if not cleaned:
                continue
            for kind, rx in PATTERNS:
                m = rx.match(cleaned)
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
