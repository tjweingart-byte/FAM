"""Nando's adversarial corpus (§363): every question three reviews used to
break him, with what is acceptable for each.

Each case is `(question, acceptable)`: the nodes Nando may file it under -
the true node, any true broader node, and "" when filing nothing is
acceptable. Anything else is a specific wrong node, the one failure that
matters (decided item 38). One list, so a fix for one case is measured
against all of them at once - the reason it exists: three rounds of fixes
each moved a few cases from wrong to right and a few others from right to
wrong, and only a fixed corpus shows both.

Written from the reviews' own examples (§361, §362, §363), labelled once.
Not an estimate of how often Nando is right in production - that is piece
9's judged set - but a floor no change may fall below.
"""

SPORT = ("sports",)
#: (question, acceptable nodes). "" means nothing filed is acceptable.
CASES = (
    # names nothing decides: nothing, or what the readings truly share
    ("Rangers season preview", ("",) + SPORT),
    ("Panthers season preview", ("",) + SPORT),
    ("Cardinals win", ("",) + SPORT),
    ("how did the Kings do", ("",)),
    ("how did United do", ("",)),
    # a third meaning the table never filed
    ("cardinals gather to elect a new pope", ("",)),
    ("tech giants earnings", ("", "business", "markets", "stock market", "money", "tech")),
    ("kings and queens of England", ("",)),
    ("the Book of Kings", ("", "books", "culture")),
    ("Kings of Leon tour", ("", "live touring", "music industry", "culture")),
    ("Power Rangers movie", ("", "film", "box office", "culture", "film franchises")),
    ("Power Rangers game", ("", "video games", "tech", "culture")),
    ("Power Rangers video game review", ("", "video games", "tech", "culture")),
    ("park rangers rescue hikers near the stadium", ("",)),
    ("Black Panthers history", ("",)),
    ("Black Panthers history game", ("",)),
    ("Giants Causeway", ("",)),
    ("giants of jazz", ("", "music industry", "culture")),
    ("Texas Rangers arrest suspect", ("",)),
    ("St Louis cardinals bird", ("",)),
    ("Florida panthers endangered", ("",)),
    ("bald eagles return to nest along the river", ("",)),
    ("United States airport security rules", ("", "air travel", "airlines", "business",
                                              "flights")),
    ("United Nations flights of aid", ("", "air travel", "airlines", "business", "flights")),
    ("Utah jazz festival lineup", ("", "live touring", "music industry", "culture")),
    ("jazz festival lineup", ("", "live touring", "music industry", "culture")),
    ("jazz brunch near me this season", ("", "restaurants", "food scene", "food culture",
                                         "culture", "local restaurants")),
    # names decided by the words around them
    ("Miami heat wave this week", ("extreme weather",)),
    ("Heat beat the Celtics in the playoffs", ("nba",)),
    ("hurricanes hitting the Carolina coast", ("extreme weather",)),
    ("Hurricanes goalie pulled in the third", ("nhl",)),
    ("United's new striker scores on debut", ("premier league",)),
    ("United cancels flights at Newark", ("airlines", "flights")),
    ("Manchester United States tour game", ("premier league", "football", "") + SPORT),
    ("Apple unveils a new iPhone", ("smartphones",)),
    ("stocks to watch Apple", ("stock market", "markets", "money", "")),
    ("Amazon warehouse workers strike", ("retail business",)),
    ("New York Rangers trade", ("new york rangers",)),
    ("New York Giants roster", ("nfl",)),
    ("Private jets for billionaires", ("air travel", "airlines", "business", "")),
    ("Texas Rangers score", ("mlb", "baseball", "") + SPORT),
    ("Texas Rangers division lead after Sunday's game", ("mlb", "baseball", "") + SPORT),
    ("St. Louis Cardinals score", ("mlb", "baseball", "") + SPORT),
    ("Florida Panthers win", ("nhl", "ice hockey", "") + SPORT),
    ("Kings game tonight", ("",) + SPORT),
    ("Rangers game preview", ("",) + SPORT),
    # two names in one question
    ("Eagles fans brave the heat wave at the game",
     ("philadelphia eagles", "nfl", "american football", "extreme weather", "") + SPORT),
    ("eagles nest and migration seen from the stadium during heat wave temperatures",
     ("", "extreme weather", "climate science", "science")),
    ("Apple orchard heat wave", ("", "extreme weather", "climate science", "science")),
    ("Apple shares fall as heat wave hits",
     ("", "stock market", "markets", "money", "extreme weather", "climate science", "science")),
    ("bald eagles nest survives heat wave", ("", "extreme weather", "climate science", "science")),
    ("Mercury levels in fish rise in heat wave",
     ("", "extreme weather", "climate science", "science", "public health", "medicine",
      "health")),
    ("Lakers game in the heat wave", ("", "los angeles lakers", "nba", "basketball") + SPORT),
    ("Giants earnings and Apple stock", ("", "stock market", "markets", "money")),
    # seeded teams: the seed's rule is that these names mean the team
    ("Dallas Cowboys news", ("dallas cowboys",)),
    ("Boston Red Sox trade rumors", ("boston red sox",)),
    ("Utah Jazz vs Lakers", ("utah jazz", "nba", "basketball") + SPORT),
    ("New York Yankees", ("new york yankees",)),
    ("Green Bay Packers", ("green bay packers",)),
    ("Boston Celtics", ("boston celtics",)),
    ("Toronto Maple Leafs", ("toronto maple leafs",)),
    ("bengals", ("bengals",)),
    ("steelers news", ("steelers",)),
    ("Detroit Tigers manager fired", ("detroit tigers",)),
    ("what happened to the Detroit Lions", ("detroit lions",)),
    ("Lakers game tonight", ("los angeles lakers",)),
    ("Stanley Cup odds", ("stanley cup",)),
)

#: Go Deeper parent titles: (title, acceptable prior nodes).
PARENTS = (
    ("Philadelphia Eagles' playoff picture", ("philadelphia eagles",)),
    ("The Dallas Cowboys' offseason overhaul", ("dallas cowboys",)),
    ("Boston Celtics rebuild after the trade", ("boston celtics",)),
    ("Utah's jazz festival lineup", ("", "live touring", "music industry", "culture")),
)

#: (text, is a result) for C3P0's checks. **Missing a guessed result is
#: the worse mistake** (the owner, §364), so every result form must be
#: caught; `ACCEPTED_FLAGS` are innocent texts flagged by design - each
#: only sends that search back to the question itself.
RESULTS = (
    ("Eagles beat Giants 24-17", True), ("Eagles 31-10 win", True),
    ("Chiefs topped Bills", True), ("Eagles Crush Giants", True),
    ("Real Madrid has won the Champions League", True),
    ("Dodgers have clinched the pennant", True), ("Spain has won Euro 2024", True),
    ("Eagles lost to the Giants", True), ("Eagles lost on Sunday", True),
    ("Eagles 24, Giants 17", True), ("Eagles win Super Bowl", True),
    ("Celtics won game 7 of the NBA Finals", True), ("Celtics won 4 games to 1", True),
    ("Mahomes returns to the field as Chiefs beat Bills", True),
    ("Who will replace Klopp after Liverpool crushed Everton", True),
    ("Dodgers sweep the Padres", True), ("Rangers have been eliminated", True),
    ("Eagles defeat Giants", True),
    ("Eagles Giants final score, Sunday 11 October 2026", False),
    ("Eagles Giants final score 2026-10-11", False), ("October 10-11", False),
    ("10-11 October", False), ("Oct 10-12 schedule", False), ("Ages 18-25 voting", False),
    ("5-7 day forecast", False), ("top 10-15 players", False), ("9-11 attacks", False),
    ("9-11 memorial", False), ("a 50-50 chance", False), ("the 4-3 defense", False),
    ("Boeing 737-800", False), ("rated 4-5 stars", False), ("9-5 job", False),
    ("won't", False), ("who won", False), ("Did the Eagles win", False),
    ("Can the Chiefs beat the Bills?", False), ("Will Eagles beat Giants", False),
    ("how many games have the Eagles won", False),
    ("the Eagles have won four straight", False),
    ("Eagles Giants preview, Sunday 1pm", False), ("what to watch in Eagles Giants", False),
)
#: Innocent texts the check flags by design (§364).
ACCEPTED_FLAGS = (
    "Lost in Translation review", "I lost my job", "lose weight fast",
    "swept away by the flood", "eliminated tariffs on steel",
    "Windows 11, Office 365 compared", "Has Spain ever won the World Cup",
)

#: (person, request, asks for that person's view).
VIEWS = (
    ("Paul Krugman", "What does Paul Krugman think about the tariffs?", True),
    ("Krugman", "Krugman's take on inflation", True),
    ("Krugman", "Krugman’s take on inflation", True),
    ("Krugman", "the opinion of Krugman on rates", True),
    ("Taylor Swift", "what did Taylor Swift say about the election", True),
    ("Ron", "what does Byron think", False),
    ("Biden", "Biden said GDP grew 3%, true?", False),
    ("Biden", "Biden says he will veto the bill", False),
    ("Biden", "Biden feels the heat over polls", False),
    ("Taylor Swift", "How many views did Taylor Swift's video get", False),
    ("Taylor Swift", "Taylor Swift's views count on YouTube", False),
    ("Krugman", "Krugman's position in the rankings", False),
    ("Biden", "Do people think Biden is too old", False),
    ("Michael Jordan", "Jordan thinks about peace with Israel", False),
)
