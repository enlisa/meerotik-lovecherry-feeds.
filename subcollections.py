"""Sub-collection structure (approved by the owner) with DE/IT/FR/EN titles and name rules.
Each entry: handle -> (parent_collection_handle, {de,it,fr,en}, regex on product name)."""
import re


def R(*w):
    return re.compile("|".join(w), re.I)


def T(de, it, fr, en):
    return {"de": de, "it": it, "fr": fr, "en": en}


SUBS = {
    # ---- Toys
    "toys-vibratoren": ("toys", T("Vibratoren", "Vibratori", "Vibromasseurs", "Vibrators"),
                        R(r"vibrator", r"\bvibe\b", r"vibrating(?! penis ring| cock)", r"rabbit", r"g-spot")),
    "toys-dildos": ("toys", T("Dildos", "Dildo", "Godemichés", "Dildos"), R(r"dildo", r"\bdong\b", r"realistic (penis|cock)")),
    "toys-masturbatoren": ("toys", T("Masturbatoren", "Masturbatori", "Masturbateurs", "Masturbators"),
                           R(r"masturbat", r"stroker", r"fleshlight", r"tenga", r"pocket pussy", r"\bpussy\b", r"\bvagina\b", r"torso")),
    "toys-anal-spielzeug": ("toys", T("Anal-Spielzeug", "Giocattoli anali", "Jouets anaux", "Anal Toys"),
                            R(r"butt ?plug", r"anal (plug|beads|trainer|vibr|dildo|probe|hook|toy)", r"prostate", r"p-spot", r"\bplug\b")),
    "toys-penisringe": ("toys", T("Penisringe", "Anelli per il pene", "Anneaux péniens", "Cock Rings"),
                        R(r"cock ?ring", r"penis ring", r"c-ring")),
    "toys-kegel-liebeskugeln": ("toys", T("Kegel- & Liebeskugeln", "Palline vaginali e Kegel", "Boules de Geisha et Kegel", "Kegel & Love Balls"),
                                R(r"kegel", r"ben wa", r"love balls?", r"smartballs?", r"geisha balls")),
    "toys-klitoris-stimulatoren": ("toys", T("Klitoris-Stimulatoren", "Stimolatori clitoridei", "Stimulateurs clitoridiens", "Clitoral Stimulators"),
                                   R(r"clitor", r"clit\b", r"air pulse", r"sucker", r"suction")),
    "toys-eier-bullets": ("toys", T("Eier & Bullets", "Ovuli e bullet", "Œufs et bullets", "Eggs & Bullets"),
                          R(r"bullet", r"\beggs?\b", r"lipstick")),
    "toys-massagegeraete-wands": ("toys", T("Massagegeräte & Wands", "Massaggiatori e wand", "Masseurs et wands", "Massagers & Wands"),
                                  R(r"\bwand\b", r"massager")),
    "toys-pumpen": ("toys", T("Pumpen", "Pompe", "Pompes", "Pumps"), R(r"\bpump\b", r"enlarg")),
    "toys-strap-ons-harnesse": ("toys", T("Strap-ons & Harnesse", "Strap-on e imbracature", "Strap-ons et harnais", "Strap-Ons & Harnesses"),
                                R(r"strap-?on", r"harness", r"hollow")),
    "toys-stimulatoren": ("toys", T("Stimulatoren", "Stimolatori", "Stimulateurs", "Stimulators"),
                          R(r"stimulat(?!ing (gel|cream|spray))", r"rotating", r"thrusting")),
    "toys-penis-huellen-verlaengerer": ("toys", T("Penis-Hüllen & Verlängerer", "Guaine ed estensioni per il pene", "Gaines et extensions péniennes", "Penis Sleeves & Extenders"),
                                        R(r"sweller", r"extension", r"penis sleeve", r"extender")),
    # ---- Lingerie (Corsagen & Bustiers merged into Sets & Bodys)
    "lingerie-slips-strings": ("lingerie", T("Slips & Strings", "Slip e perizomi", "Culottes et strings", "Briefs & Thongs"),
                               R(r"\bstring\b", r"loincloth", r"taddy", r"\bslip\b", r"hotpants", r"\bshorts?\b", r"loopstring", r"thong",
                                  r"panties", r"\bpanty\b", r"\bbriefs?\b", r"g-string", r"c-string", r"\btanga\b")),
    "lingerie-struempfe-strapse": ("lingerie", T("Strümpfe & Strapse", "Calze e reggicalze", "Bas et porte-jarretelles", "Stockings & Garters"),
                                   R(r"stocking", r"garter", r"suspender", r"hold-up", r"pantyhose", r"\btights\b")),
    "lingerie-bhs-oberteile": ("lingerie", T("BHs & Oberteile", "Reggiseni e top", "Soutiens-gorge et hauts", "Bras & Tops"),
                               R(r"\bbras?\b", r"\btop\b", r"nipple covers?", r"pasties")),
    "lingerie-sets-bodys": ("lingerie", T("Sets, Bodys & Corsagen", "Set, body e corsetti", "Ensembles, bodys et corsets", "Sets, Bodysuits & Corsets"),
                            R(r"\bset\b", r"\bbody\b", r"bodystocking", r"teddy", r"babydoll", r"chemise", r"catsuit", r"romper",
                               r"\bpieces?\b", r"corset", r"basque", r"bustier")),
    "lingerie-kostueme-kleider": ("lingerie", T("Kostüme & Kleider", "Costumi e abiti", "Costumes et robes", "Costumes & Dresses"),
                                  R(r"costume", r"\bdress\b", r"\bskirt\b", r"\bgown\b", r"negligee", r"nightie", r"\brobe\b")),
    "lingerie-herren-unterwaesche": ("lingerie", T("Herren-Unterwäsche", "Intimo uomo", "Sous-vêtements homme", "Men's Underwear"),
                                     R(r"\bjock", r"\bboxers?\b", r"underwear", r"pouch")),
    "lingerie-harnesse-accessoires": ("lingerie", T("Harnesse & Accessoires", "Imbracature e accessori", "Harnais et accessoires", "Harnesses & Accessories"),
                                      R(r"harness", r"gloves?", r"tassle", r"tassel", r"waist chain", r"\bchain\b", r"\bwig\b", r"\bmask")),
    # ---- BDSM
    "bdsm-handschellen-fesseln": ("bdsm", T("Handschellen & Fesseln", "Manette e legacci", "Menottes et entraves", "Cuffs & Restraints"),
                                  R(r"cuffs?", r"restraint", r"bondage (tape|rope|set|kit)", r"\brope\b", r"spreader", r"hog ?tie", r"bind me", r"shackle")),
    "bdsm-peitschen-paddles": ("bdsm", T("Peitschen & Paddles", "Fruste e paddle", "Fouets et paddles", "Whips & Paddles"),
                               R(r"whip", r"flogger", r"paddle", r"\bcrop\b", r"feather", r"spank")),
    "bdsm-halsbaender-leinen": ("bdsm", T("Halsbänder & Leinen", "Collari e guinzagli", "Colliers et laisses", "Collars & Leashes"),
                                R(r"collar", r"leash")),
    "bdsm-knebel-masken": ("bdsm", T("Knebel & Masken", "Bavagli e maschere", "Bâillons et masques", "Gags & Masks"),
                           R(r"gag\b", r"\bmask", r"blindfold", r"\bhood\b", r"muzzle")),
    "bdsm-nippelklemmen": ("bdsm", T("Nippelklemmen", "Pinze per capezzoli", "Pinces à tétons", "Nipple Clamps"), R(r"clamp")),
    "bdsm-keuschheit": ("bdsm", T("Keuschheit", "Castità", "Chasteté", "Chastity"), R(r"chastity", r"cock cage")),
    "bdsm-elektrostimulation": ("bdsm", T("Elektrostimulation", "Elettrostimolazione", "Électrostimulation", "Electro Stimulation"),
                                R(r"electro", r"\bshock")),
    "bdsm-geschirre-outfits": ("bdsm", T("Geschirre & Outfits", "Imbracature e outfit", "Harnais et tenues", "Harnesses & Outfits"),
                               R(r"harness", r"\bbra\b", r"thong", r"\bbasque\b", r"corset", r"briefs?\b", r"panties", r"pasties")),
    "bdsm-zubehoer-spezial": ("bdsm", T("Zubehör & Spezial", "Accessori e speciali", "Accessoires et spécial", "Accessories & Specialty"),
                              R(r"\bchain\b", r"\bhook\b", r"funnel", r"urethral", r"pinwheel", r"stethoscope", r"candle", r"\btacks?\b", r"tickler")),
    # ---- Für Paare
    "paare-spiele-wuerfel": ("fuer-paare", T("Spiele & Würfel", "Giochi e dadi", "Jeux et dés", "Games & Dice"),
                             R(r"\bgames?\b", r"\bdice\b", r"\bdie\b", r"roulette", r"cards?\b", r"board", r"checkers", r"domino", r"kamasutra", r"truth or dare")),
    "paare-paar-vibratoren": ("fuer-paare", T("Paar-Vibratoren", "Vibratori di coppia", "Vibromasseurs pour couples", "Couples Vibrators"),
                              R(r"couples?")),
    "paare-gutscheine": ("fuer-paare", T("Gutscheine", "Buoni", "Bons", "Coupons"), R(r"coupons?", r"checkbook")),
    "paare-buecher": ("fuer-paare", T("Bücher", "Libri", "Livres", "Books"), R(r"\bbook\b", r"guide")),
    # ---- Wellness (Gleitmittel stay under Wellness)
    "wellness-gleitmittel": ("wellness", T("Gleitmittel", "Lubrificanti", "Lubrifiants", "Lubricants"),
                             R(r"lubric", r"lube", r"\bglide\b", r"\blub\b", r"slide gel")),
    "wellness-massageoele-gele": ("wellness", T("Massageöle & -gele", "Oli e gel da massaggio", "Huiles et gels de massage", "Massage Oils & Gels"),
                                  R(r"massage (oil|gel|cream|creme|lotion)", r"massage", r"hot oil", r"\boil\b")),
    "wellness-massagekerzen": ("wellness", T("Massagekerzen", "Candele da massaggio", "Bougies de massage", "Massage Candles"), R(r"candle")),
    "wellness-pheromone-parfuem": ("wellness", T("Pheromone & Parfüm", "Feromoni e profumi", "Phéromones et parfums", "Pheromones & Perfume"),
                                   R(r"pheromone", r"perfume", r"incense", r"inciense")),
    "wellness-stimulation-verzoegerung": ("wellness", T("Stimulation & Verzögerung", "Stimolazione e ritardo", "Stimulation et retardants", "Stimulation & Delay"),
                                          R(r"cream", r"delay", r"arousal", r"stimulat", r"erect", r"tightening", r"orgasm", r"climax")),
    "wellness-reinigung-intimpflege": ("wellness", T("Reinigung & Intimpflege", "Pulizia e igiene intima", "Nettoyage et soins intimes", "Cleaning & Intimate Care"),
                                       R(r"clean", r"talcum", r"soap", r"wipes?", r"intimate", r"hygien", r"moisturi")),
    "wellness-bad-koerperpflege": ("wellness", T("Bad & Körperpflege", "Bagno e cura del corpo", "Bain et soins du corps", "Bath & Body Care"),
                                   R(r"\bbath\b", r"lotion", r"body (spray|paint)", r"shower", r"foam")),
    "wellness-beckenboden-kegelkugeln": ("wellness", T("Beckenboden & Kegelkugeln", "Pavimento pelvico e palline Kegel", "Plancher pelvien et boules de Kegel", "Pelvic Floor & Kegel Balls"),
                                         R(r"kegel", r"smartball", r"brazilian balls", r"love bird")),
}
