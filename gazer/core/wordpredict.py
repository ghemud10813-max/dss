"""Tiny adaptive word predictor for the gaze keyboard."""

from __future__ import annotations

import unicodedata


def _is_word(w: str) -> bool:
    return len(w) >= 2 and any(c.isalpha() for c in w) and all(
        c.isalpha() or c == "'" or unicodedata.category(c) in ("Mn", "Mc") for c in w)

_BASE = """
the be to of and a in that have i it for not on with he as you do at this but his by from they we
say her she or an will my one all would there their what so up out if about who get which go me when
make can like time no just him know take people into year your good some could them see other than
then now look only come its over think also back after use two how our work first well way even new
want because any these give day most us is are was were been has had did does going thing things
very really right still here where why should need feel try leave call keep let begin seem help talk
turn start show hear play run move live believe hold bring happen write provide sit stand lose pay
meet include continue set learn change lead understand watch follow stop create speak read allow add
spend grow open walk win offer remember love consider appear buy wait serve die send expect build stay
fall cut reach kill remain suggest raise pass sell require report decide pull hello hi thanks thank
please yes okay ok sorry great nice sure maybe tomorrow today tonight morning evening night week month
home house room door window water food coffee tea eat drink sleep bed chair table computer phone email
message text send reply meeting call video music movie game book page link search google youtube
friend family mother father brother sister son daughter child children baby man woman boy girl name
doctor nurse medicine pain help need want hungry thirsty tired cold hot warm happy sad angry bored
problem question answer idea place world country city school class teacher student job money price
number part point hand eye head face body word line side end kind case week program fact group
problem information system government company business service life old great high small large next
early young important few public bad same able last long little own right big different best better
sure free full special easy clear recent certain personal hard low late general real major possible
something nothing everything anything someone everyone always never often sometimes again already
soon later before during between under around without within through against among toward
""".split() + """
hai hain kya nahi nahin haan acha accha theek thik kaise kyun kyon kab kahan kaun mera meri mere tera teri
tere apna apni hum tum aap main mujhe tujhe usko isko yeh woh wahi abhi kal aaj parso bahut thoda zyada
bhai yaar dost ghar kaam paani khana chalo chal jaldi dheere please shukriya dhanyavaad namaste haanji
matlab samajh pata chahiye sakta sakti karna karo kiya hua ho gaya raha rahi wala wali bas aur lekin phir
""".split()


class WordPredictor:
    """Prefix completion ranked by a base list + your own words, and next-word
    suggestions learned from pairs you type (stored as "prev>word" keys)."""

    def __init__(self, learned: dict[str, int] | None = None):
        n = len(_BASE)
        self.base = {}
        for i, w in enumerate(_BASE):
            self.base.setdefault(w, n - i)
        data = dict(learned or {})
        self.pairs: dict[str, int] = {k: v for k, v in data.items() if ">" in k}
        self.learned: dict[str, int] = {k: v for k, v in data.items() if ">" not in k}
        self.prev = ""

    def score(self, word: str) -> float:
        return self.base.get(word, 0) + self.learned.get(word, 0) * 60

    def suggest(self, prefix: str, n: int = 4, prev: str | None = None) -> list[str]:
        p = prefix.lower()
        prev = (self.prev if prev is None else prev).lower()
        follow = {k.split(">", 1)[1]: v for k, v in self.pairs.items() if k.startswith(prev + ">")} if prev else {}
        cands = set(self.base) | set(self.learned) | set(follow)
        if p:
            cands = {w for w in cands if w.startswith(p) and w != p}
        ranked = sorted(cands, key=lambda w: (-(follow.get(w, 0) * 500 + self.score(w)), len(w), w))
        return ranked[:n]

    def learn(self, word: str) -> None:
        w = word.strip().lower()
        if _is_word(w):
            self.learned[w] = self.learned.get(w, 0) + 1
            if self.prev:
                key = f"{self.prev}>{w}"
                self.pairs[key] = self.pairs.get(key, 0) + 1
            self.prev = w
        else:
            self.prev = ""

    def end_sentence(self) -> None:
        self.prev = ""

    def export(self) -> dict[str, int]:
        """Everything worth saving to the profile (words + learned pairs)."""
        return {**self.learned, **self.pairs}
