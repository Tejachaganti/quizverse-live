"""Small, reviewed general-knowledge bank used by the server engine."""

import hashlib
from dataclasses import dataclass

from quizverse.taxonomy import legacy_category_slug, resolve_category_slug


@dataclass(frozen=True, slots=True)
class Question:
    category: str
    prompt: str
    options: tuple[str, str, str, str]
    answer: int
    id: str = ""
    category_id: str | None = None
    difficulty: str = "medium"
    language: str = "en"
    learning_levels: tuple[str, ...] = ()
    review_status: str = "reviewed"
    question_format: str = "multiple_choice"
    exam_collection_ids: frozenset[str] = frozenset()
    provenance: str = "built_in_reviewed"
    format_payload: dict[str, object] | None = None
    explanation: str | None = None
    topic: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", hashlib.sha256(self.prompt.encode("utf-8")).hexdigest())
        if self.category_id is None:
            slug = resolve_category_slug(self.category, self.provenance) or legacy_category_slug(self.category, self.provenance)
            if slug:
                object.__setattr__(self, "category_id", f"category:{slug}")
        if not self.topic:
            object.__setattr__(self, "topic", self.category.lower() if self.category else "")


_ORIGINAL_QUESTIONS: tuple[Question, ...] = (
    Question("Science", "Which planet is known as the Red Planet?", ("Venus", "Mars", "Jupiter", "Mercury"), 1, explanation="Mars is known as the Red Planet because iron minerals in its soil oxidize, or rust, giving it a reddish appearance."),
    Question("Geography", "What is the capital city of Australia?", ("Sydney", "Perth", "Canberra", "Melbourne"), 2, explanation="Canberra was selected as the federal capital of Australia in 1908 as a compromise between Sydney and Melbourne."),
    Question("Technology", "During a program's execution, which component performs its instruction cycle?", ("Central Processing Unit", "Computer Power Utility", "Core Program Unit", "Central Performance User"), 0, explanation="The Central Processing Unit fetches, decodes, and executes program instructions."),
    Question("Nature", "Among mammals alive today, which species reaches the greatest size?", ("African elephant", "Blue whale", "Giraffe", "Orca"), 1, explanation="The blue whale is the largest known mammal and animal ever to have lived on Earth, reaching lengths of up to 30 meters."),
    Question("Space", "Which planet is famous for its prominent rings?", ("Neptune", "Earth", "Saturn", "Mars"), 2, explanation="Saturn has the most extensive and visually prominent planetary ring system in the Solar System, composed primarily of water ice with a trace of rocky material."),
    Question("Literature", "Who wrote the play Romeo and Juliet?", ("Charles Dickens", "Jane Austen", "William Shakespeare", "Mark Twain"), 2, explanation="The tragedy is attributed to William Shakespeare and was written in the late sixteenth century.", topic="Shakespearean drama"),
    Question("Science", "What is the chemical symbol for gold?", ("Ag", "Gd", "Go", "Au"), 3, explanation="Au comes from gold's Latin name aurum; Ag is the symbol for silver.", topic="chemical symbols"),
    Question("Geography", "Which ocean basin occupies the greatest area on Earth's surface?", ("Atlantic Ocean", "Indian Ocean", "Pacific Ocean", "Arctic Ocean"), 2, explanation="The Pacific Ocean covers more area than any other ocean basin.", topic="oceans"),
    Question("History", "The ancient Olympic Games originated in which country?", ("Italy", "Greece", "Egypt", "Turkey"), 1, explanation="The ancient festival was held at Olympia in Greece.", topic="ancient Greece"),
    Question("Art", "How many keys are there on a standard modern piano?", ("66", "72", "88", "92"), 2, explanation="A standard full-size piano spans 88 keys: 52 white and 36 black.", topic="musical instruments"),
    Question("Science", "Which gas do plants absorb from the atmosphere?", ("Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"), 2, explanation="Plants use carbon dioxide as a carbon source during photosynthesis.", topic="photosynthesis"),
    Question("General", "How many sides does a hexagon have?", ("Five", "Six", "Seven", "Eight"), 1, explanation="A hexagon is a polygon with six sides and six angles.", topic="polygon properties"),
    Question("Technology", "Which language is primarily used to style web pages?", ("HTML", "CSS", "SQL", "Python"), 1, explanation="CSS controls the presentation and layout of HTML documents.", topic="web technologies"),
    Question("Geography", "Which country is home to the city of Kyoto?", ("China", "South Korea", "Japan", "Thailand"), 2, explanation="Kyoto is a Japanese city that served as Japan's capital for many centuries.", topic="East Asian cities"),
    Question("Nature", "What is the fastest land animal?", ("Lion", "Cheetah", "Pronghorn", "Horse"), 1, explanation="The cheetah can reach the highest short-distance running speeds of any land animal.", topic="animal adaptations"),
    Question("Science", "What is the boiling point of water at sea level?", ("90 degrees C", "100 degrees C", "110 degrees C", "120 degrees C"), 1, explanation="At standard atmospheric pressure, pure water boils at 100 degrees Celsius; the boiling point changes with pressure.", topic="phase changes"),
    Question("General", "How many colors are traditionally named in a rainbow?", ("Five", "Six", "Seven", "Eight"), 2, explanation="The traditional convention names seven bands: red, orange, yellow, green, blue, indigo, and violet.", topic="visible light"),
    Question("History", "Which civilization built Machu Picchu?", ("Maya", "Aztec", "Inca", "Roman"), 2, explanation="Machu Picchu was built by the Inca in the Andes of present-day Peru.", topic="Andean history"),
    Question("Technology", "What does URL stand for?", ("Universal Reference Link", "Uniform Resource Locator", "Unified Routing Language", "User Resource List"), 1, explanation="A URL identifies the location of a resource and how it can be accessed.", topic="web addressing"),
    Question("Science", "Which organ pumps blood around the human body?", ("Liver", "Lung", "Brain", "Heart"), 3, explanation="The heart contracts to move blood through the lungs and the rest of the circulatory system.", topic="human circulation"),
)


def _reviewed_question(category, topic, difficulty, prompt, options, answer, explanation):
    """Create an authored item using an existing official taxonomy category."""
    slug = resolve_category_slug(category)
    if slug is None:
        raise ValueError(f"Built-in question category {category!r} is not official.")
    return Question(slug, prompt, options, answer, category_id=f"category:{slug}",
                    difficulty=difficulty, explanation=explanation, topic=topic)


# Distinct subtopics and real reasoning/application angles; no paraphrase-only
# variants of the original twenty items are included.
_EXPANDED_QUESTIONS: tuple[Question, ...] = (
    # Technology & Computing (6)
    _reviewed_question("Computing Fundamentals", "data representation", "easy", "How many bits make up one byte in the standard definition used by modern computers?", ("4", "8", "10", "16"), 1, "A byte is conventionally eight bits and can represent 256 bit patterns."),
    _reviewed_question("Computing Fundamentals", "memory", "medium", "Which memory loses its contents when a computer is switched off?", ("RAM", "SSD", "Flash memory", "ROM"), 0, "Ordinary system RAM is volatile and requires power to retain its contents."),
    _reviewed_question("Programming", "iteration", "medium", "In Python, what values does `range(2, 7, 2)` produce?", ("2, 4, 6", "2, 4, 6, 8", "3, 5, 7", "2, 3, 4, 5, 6, 7"), 0, "The start is included, the stop is excluded, and the step is two."),
    _reviewed_question("Programming", "algorithm analysis", "hard", "If an algorithm checks every distinct pair among n items once, how does its comparison count grow?", ("O(n)", "O(log n)", "O(n²)", "O(1)"), 2, "There are n(n−1)/2 distinct pairs, which grows proportionally to n squared."),
    _reviewed_question("Internet & Networks", "network addressing", "easy", "What does DNS do when a browser visits a site by domain name?", ("Encrypt the page", "Resolve the name to an IP address", "Store the page permanently", "Choose the page language"), 1, "DNS resolves a human-readable domain name to a network address."),
    _reviewed_question("Digital Safety", "authentication", "medium", "Why can a second authentication factor help when a password is stolen?", ("It lengthens the password", "The attacker also needs the separate factor", "It hides the account name", "It prevents every phishing attack"), 1, "A second proof means a stolen password alone is insufficient, although it cannot prevent every attack."),
    # Mathematics (6)
    _reviewed_question("Arithmetic", "proportions", "easy", "A recipe uses 3 cups of flour for 12 servings. How much is needed for 20 servings at the same ratio?", ("4 cups", "5 cups", "6 cups", "8 cups"), 1, "Each serving uses one quarter cup, so twenty servings need five cups."),
    _reviewed_question("Arithmetic", "percentages", "medium", "A jacket costs $80 and is discounted by 25%. What is its price before tax?", ("$20", "$55", "$60", "$75"), 2, "One quarter of $80 is $20; subtracting the discount leaves $60."),
    _reviewed_question("Arithmetic", "rates", "hard", "A pump fills 3/5 of a tank in 12 minutes at a constant rate. How long for the whole tank?", ("15 minutes", "18 minutes", "20 minutes", "24 minutes"), 2, "The rate is 1/20 tank per minute, so a full tank takes 20 minutes."),
    _reviewed_question("Algebra & Geometry", "linear equations", "easy", "Solve 4x − 7 = 13. What is x?", ("3", "4", "5", "6"), 2, "Adding seven gives 4x = 20; dividing by four gives x = 5."),
    _reviewed_question("Algebra & Geometry", "geometry", "medium", "A rectangle has perimeter 30 cm and length 9 cm. What is its width?", ("3 cm", "6 cm", "9 cm", "12 cm"), 1, "Half the perimeter is 15 cm; subtracting the 9 cm length leaves a 6 cm width."),
    _reviewed_question("Quantitative Aptitude", "probability", "hard", "A fair six-sided die is rolled twice. What is the probability that the sum is 7?", ("1/12", "1/9", "1/6", "1/3"), 2, "Six of the 36 equally likely ordered outcomes sum to seven, so the probability is 1/6."),
    # Reasoning (6)
    _reviewed_question("Patterns & Sequences", "number patterns", "easy", "What number comes next: 5, 10, 20, 40, …?", ("45", "60", "75", "80"), 3, "Each term doubles, so the next is 80."),
    _reviewed_question("Patterns & Sequences", "alternating patterns", "medium", "What comes next: 2, 5, 3, 6, 4, 7, …?", ("5", "6", "8", "9"), 0, "The sequence alternates plus three and minus two; after seven comes five."),
    _reviewed_question("Patterns & Sequences", "square numbers", "hard", "Which value completes 1, 4, 9, 16, 25, …?", ("30", "32", "36", "49"), 2, "These are consecutive squares; the next is 6² = 36."),
    _reviewed_question("Analytical Problems", "ordering", "easy", "Mina finishes before Jo, and Jo finishes before Lee. Who finishes last?", ("Mina", "Jo", "Lee", "Cannot be determined"), 2, "Both order relations place Lee after the other two."),
    _reviewed_question("Analytical Problems", "counting constraints", "medium", "A bus has 18 seats. Seven are occupied, then four board and two leave. How many seats are empty?", ("7", "9", "11", "13"), 1, "There are 7 + 4 − 2 = 9 passengers, leaving nine empty seats."),
    _reviewed_question("Logic Puzzles", "deduction", "hard", "A box contains only red or blue balls. If no ball is red, what must be true of every ball in the box?", ("It is blue", "There are no balls", "There is exactly one blue ball", "The box is empty"), 0, "The two stated colors exhaust the possibilities; every ball present must be blue."),
    # Verbal (6)
    _reviewed_question("Vocabulary", "synonyms", "easy", "Which word is closest in meaning to `concise`?", ("Brief", "Uncertain", "Polite", "Complex"), 0, "Concise writing expresses its meaning in few, carefully chosen words."),
    _reviewed_question("Vocabulary", "context clues", "medium", "In “The trail was arduous, so the hikers rested often,” what does `arduous` mean?", ("Well marked", "Difficult and tiring", "Short and safe", "Newly built"), 1, "The need for frequent rests indicates that arduous means demanding or tiring."),
    _reviewed_question("Grammar & Usage", "subject-verb agreement", "easy", "Complete the sentence: “The list of supplies ___ on the desk.”", ("are", "were", "is", "have been"), 2, "The subject is singular `list`; the nearby plural `supplies` does not change agreement."),
    _reviewed_question("Grammar & Usage", "punctuation", "medium", "Which sentence correctly punctuates a direct question?", ("She asked where, was the station.", "She asked, “Where is the station?”", "She asked “where is the station”.", "She asked; “Where is the station”"), 1, "The quoted words are a direct question, so the question mark belongs inside the quotation marks."),
    _reviewed_question("Reading Comprehension", "evidence", "hard", "A report says a town planted 500 trees but gives no survival data. Which conclusion is supported?", ("All 500 survived", "The town planted 500 trees", "Forest cover increased", "The trees are native"), 1, "Only the planting count is stated; survival, ecological impact, and species are not established."),
    _reviewed_question("Grammar & Usage", "clear reference", "hard", "Which revision removes the ambiguous pronoun?", ("When Sara called Priya, Sara was driving.", "When Sara called Priya, she was driving.", "Sara called Priya while driving, and she did too.", "When she called her, Sara drove."), 0, "Repeating Sara's name makes the intended driver explicit."),
    # Science (6)
    _reviewed_question("Biology", "cell structure", "easy", "Which structure contains most genetic material in a eukaryotic cell?", ("Nucleus", "Ribosome", "Cell membrane", "Golgi apparatus"), 0, "Most eukaryotic DNA is in the nucleus, although mitochondria and chloroplasts also contain DNA."),
    _reviewed_question("Biology", "ecosystem energy", "medium", "What is the primary energy source for most producers such as green plants?", ("Soil minerals", "Sunlight", "Decomposers", "Oxygen"), 1, "Plants capture light through photosynthesis and convert it to chemical energy."),
    _reviewed_question("Chemistry", "atomic structure", "easy", "Which subatomic particle carries a negative electric charge?", ("Proton", "Neutron", "Electron", "Nucleus"), 2, "Electrons are negative; protons are positive and neutrons have no net charge."),
    _reviewed_question("Chemistry", "chemical change", "medium", "Which observation best shows that iron has chemically rusted rather than changed shape?", ("It was bent", "A new reddish-brown substance formed", "It was cut", "It warmed from handling"), 1, "Rust is iron oxide, a new substance formed through chemical reaction."),
    _reviewed_question("Physics", "Newton's laws", "hard", "A cart's mass is constant while its net force doubles. What happens to its acceleration?", ("It halves", "It stays constant", "It doubles", "It quadruples"), 2, "Newton's second law gives acceleration as net force divided by mass."),
    _reviewed_question("Space & Astronomy", "orbital motion", "hard", "Why do astronauts in orbit appear weightless while Earth's gravity still acts on them?", ("Gravity ends above air", "They and their craft are falling together", "Engines cancel gravity", "Their mass becomes zero"), 1, "Astronauts and spacecraft are in continuous free fall together around Earth."),
    # General Knowledge (6)
    _reviewed_question("Geography & Places", "latitude", "easy", "What does latitude measure on a globe?", ("Position east or west of Greenwich", "Position north or south of the Equator", "Height above sea level", "Distance to a coast"), 1, "Latitude measures angular position north or south of the Equator."),
    _reviewed_question("Geography & Places", "map scale", "medium", "A map scale is 1 cm to 5 km. What real distance does 3 cm represent?", ("8 km", "10 km", "15 km", "20 km"), 2, "Three centimeters at five kilometers per centimeter represents 15 kilometers."),
    _reviewed_question("History & Civics", "civic institutions", "easy", "What is a primary role of elected legislators in a representative democracy?", ("Interpret every court case", "Make and revise laws", "Enforce all laws personally", "Conduct scientific studies"), 1, "Legislatures debate and enact laws; courts interpret them and executive bodies administer them."),
    _reviewed_question("History & Civics", "historical sources", "medium", "Which source most directly records a resident's experience of a factory strike?", ("A later textbook", "A worker's diary written during it", "A factory map", "A modern encyclopedia"), 1, "A contemporaneous diary is a primary source, though it still requires context."),
    _reviewed_question("World Facts", "international institutions", "medium", "What is the main role of the International Court of Justice?", ("Set interest rates", "Resolve legal disputes between states", "Direct every UN agency", "Issue passports"), 1, "The ICJ settles legal disputes submitted by states and gives advisory opinions."),
    _reviewed_question("Current Affairs", "source evaluation", "hard", "Two current-affairs posts conflict. What is the strongest first step before sharing either?", ("Choose the dramatic claim", "Check recent reliable reporting and primary evidence", "Assume both are true", "Share both without context"), 1, "Corroborating claims against current reliable reporting and primary evidence helps establish what is known."),
    # Sports (6)
    _reviewed_question("Sports Rules & Events", "association football", "easy", "How many players from one team are normally on the field at the start of an association-football match?", ("9", "10", "11", "12"), 2, "A standard side fields eleven players, including its goalkeeper."),
    _reviewed_question("Sports Rules & Events", "tennis scoring", "medium", "In a standard tennis game, what score follows immediately after 30–30?", ("Deuce", "40–30", "Advantage", "Game"), 1, "The next point makes 40–30; deuce is reached at 40–40."),
    _reviewed_question("Sports Rules & Events", "basketball scoring", "easy", "How many points is a successful basketball free throw worth?", ("1", "2", "3", "It depends on distance"), 0, "A free throw is worth one point."),
    _reviewed_question("Athletes & Teams", "Olympic history", "medium", "The first modern Summer Olympic Games were held in 1896 in which city?", ("Paris", "Athens", "London", "Rome"), 1, "Athens hosted the first modern Summer Olympics in 1896."),
    _reviewed_question("Fitness & Wellbeing", "training progression", "medium", "Why should a beginner generally increase running distance gradually?", ("It guarantees no injury", "It gives tissues time to adapt to load", "It removes the need to rest", "It immediately raises speed"), 1, "Gradual increases allow adaptation and can reduce overload risk, though they cannot guarantee injury prevention."),
    _reviewed_question("Fitness & Wellbeing", "recovery", "hard", "After hard sessions, persistent fatigue and falling performance suggest which response?", ("Add more intense sessions", "Reduce load and allow recovery", "Remove all rest days", "Replace sleep with supplements"), 1, "Reducing load and prioritizing recovery is sensible when fatigue persists."),
    # Entertainment (5)
    _reviewed_question("Film & Television", "film production", "easy", "Who primarily guides performances and visual storytelling during a film's production?", ("Director", "Location scout", "Ticket seller", "Film critic"), 0, "The director guides performances and the creative interpretation of a screenplay."),
    _reviewed_question("Film & Television", "animation", "medium", "Why are many slightly different images shown rapidly in traditional animation?", ("To create perceived motion", "To improve sound", "To change resolution", "To prevent color fading"), 0, "Rapidly displayed successive images create the impression of movement."),
    _reviewed_question("Popular Music", "song form", "easy", "What is a chorus usually designed to do in a song?", ("Return as a recurring central section", "List all instruments", "Replace melody with silence", "Name the audio file"), 0, "A chorus recurs and often carries a song's central hook or idea."),
    _reviewed_question("Popular Music", "meter", "medium", "In common time, how many quarter-note beats are in one measure?", ("2", "3", "4", "6"), 2, "The 4/4 time signature groups four quarter-note beats in a measure."),
    _reviewed_question("Games & Pop Culture", "game design", "hard", "A game lets players make choices whose consequences persist. Which quality does this most directly support?", ("Player agency", "Screen refresh", "Compression", "Input latency"), 0, "Consequential choices let players influence outcomes, supporting agency."),
    # Nature (5)
    _reviewed_question("Animals", "adaptation", "easy", "What is a key function of a polar bear's thick layer of body fat?", ("Store oxygen", "Insulate and store energy", "Produce sunlight", "Prevent movement"), 1, "Blubber reduces heat loss and serves as an energy reserve."),
    _reviewed_question("Animals", "food webs", "medium", "If a predator population falls sharply, what may happen to a prey population?", ("It may increase", "It must go extinct", "It stops reproducing", "It becomes a producer"), 0, "Lower predation can allow prey to increase, though other ecosystem factors matter."),
    _reviewed_question("Plants & Ecosystems", "plant transport", "medium", "Which plant tissue carries water and minerals from roots toward leaves?", ("Phloem", "Xylem", "Epidermis", "Pollen"), 1, "Xylem carries water and minerals upward; phloem transports sugars and other products."),
    _reviewed_question("Environment & Climate", "carbon cycle", "hard", "Why can clearing a mature forest raise atmospheric CO₂ in the short term?", ("Trees contain no carbon", "Biomass carbon can be released and fewer trees remain to absorb CO₂", "Soil loses all minerals", "Clouds absorb oxygen"), 1, "Burning or decay releases biomass carbon, while fewer trees remain to take up CO₂."),
    _reviewed_question("Environment & Climate", "weather and climate", "easy", "Which statement best distinguishes climate from weather?", ("Weather is a long-term average", "Weather is short-term; climate summarizes long-term patterns", "They are identical", "Climate applies only to oceans"), 1, "Weather describes shorter-term atmospheric conditions; climate summarizes long-term patterns."),
    # Culture (5)
    _reviewed_question("Literature", "literary forms", "easy", "A memoir is primarily based on what?", ("The author's remembered experiences", "An unrelated fictional world", "Stage directions only", "A dictionary word list"), 0, "A memoir is nonfiction centered on an author's memories and perspective."),
    _reviewed_question("Literature", "narrative perspective", "medium", "A narrator who uses “I” to describe events they witnessed usually uses which point of view?", ("First person", "Second person", "Third-person omniscient", "Objective camera"), 0, "First-person narration uses the narrator's own voice, commonly signaled by “I.”"),
    _reviewed_question("Visual Arts", "color theory", "easy", "Which pair is complementary on a traditional artist's color wheel?", ("Blue and orange", "Blue and green", "Red and orange", "Yellow and green"), 0, "Blue and orange sit opposite one another on a traditional color wheel."),
    _reviewed_question("Visual Arts", "perspective", "medium", "In linear perspective, receding parallel edges appear to converge at what?", ("A vanishing point", "A color wheel", "A scale bar", "A frame rate"), 0, "Linear perspective uses vanishing points to represent depth."),
    _reviewed_question("Performing Arts", "stage lighting", "hard", "What can sharp side lighting emphasize in a moving dance performance?", ("Body shape and movement", "The script's language", "A louder voice", "The absence of choreography"), 0, "Side light can reveal contours and movement; the effect depends on angle and staging."),
    # Everyday knowledge (5)
    _reviewed_question("Health & Safety", "minor burns", "easy", "What is an appropriate immediate first-aid step for a minor burn?", ("Cool it under clean, cool running water", "Apply ice directly", "Break blisters", "Cover it with butter"), 0, "Cool running water helps limit heat injury; severe burns require urgent medical care."),
    _reviewed_question("Health & Safety", "medicine safety", "medium", "Why check active ingredients before taking two cold medicines together?", ("Their colors may clash", "Both may contain the same drug and cause an excessive dose", "It makes them work instantly", "Labels are only for pharmacists"), 1, "Combination products may duplicate an ingredient, creating an unsafe total dose."),
    _reviewed_question("Food & Cooking", "food safety", "easy", "Why keep raw chicken separate from ready-to-eat foods during preparation?", ("To prevent cross-contamination", "To make it cook faster", "To preserve color", "To increase protein"), 0, "Separating raw poultry and its juices reduces transfer of harmful microbes."),
    _reviewed_question("Practical Life Skills", "area measurement", "medium", "A rectangular room is 4 m long and 3 m wide. What is its floor area?", ("7 m²", "12 m²", "14 m²", "24 m²"), 1, "Area equals length times width: 4 × 3 = 12 square meters."),
    _reviewed_question("Money & Consumers", "simple interest", "hard", "Ignoring fees and compounding, what simple interest accrues on $1,000 at 5% per year for 2 years?", ("$50", "$100", "$105", "$1,100"), 1, "Simple interest is principal × rate × time: $1,000 × 0.05 × 2 = $100."),
)


QUESTIONS: tuple[Question, ...] = _ORIGINAL_QUESTIONS + _EXPANDED_QUESTIONS
