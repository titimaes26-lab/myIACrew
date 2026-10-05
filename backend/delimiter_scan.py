"""Équilibre des délimiteurs d'un fichier JS/TS/JSX/TSX : lecteur par états (commentaires, chaînes, templates, regex)."""

# "<" est volontairement absent : dans ce dépôt (React/TSX), un "/" suit presque
# toujours "<" comme fermeture de balise JSX (`</A>`), jamais comme opérateur "inférieur
# à" avant une regex (`x < /regex/`, un cas très rare). Le garder faisait manquer
# entièrement le contenu entre deux balises fermantes sur une même ligne (tout traité
# à tort comme un littéral regex), un faux négatif bien pire que le faux positif que ça
# évitait.
_REGEX_LITERAL_PRECEDERS = set('([{,;:=!&|?+-*%^~>') | {''}
# Mots-clés après lesquels un littéral regex peut suivre sans parenthèse/opérateur
# entre les deux (ex: `return /regex/.test(x);`) : après le dernier caractère de ces
# mots, last_significant serait sinon une simple lettre (absente de _REGEX_LITERAL_PRECEDERS),
# et le "/" suivant serait à tort traité comme une division plutôt qu'un littéral regex.
_REGEX_KEYWORD_PRECEDERS = {'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'yield', 'throw', 'delete', 'void', 'case'}

_PAIRS = {')': '(', ']': '[', '}': '{'}
_OPENING = set(_PAIRS.values())
_MAX_ISSUES = 5


class _Scanner:
    """Parcourt le contenu caractère par caractère. Chaque méthode `_step_*` renvoie True quand elle a consommé du
    texte (la boucle recommence alors), False pour laisser la main à l'étape suivante."""

    def __init__(self, content: str):
        self.content = content
        self.n = len(content)
        self.i = 0
        self.stack: list[str] = []
        self.in_string: str | None = None
        self.escaped = False
        self.in_line_comment = False
        self.in_block_comment = False
        self.issues: list[str] = []
        self.last_significant = ''
        self.word_buf: list[str] = []

    def scan(self) -> tuple[list[str], bool]:
        while self.i < self.n:
            if self._step_comment() or self._step_string() or self._step_comment_start() or self._step_regex():
                continue
            if self._step_code():
                break                                    # trop de problèmes : inutile de continuer
            if not self.content[self.i].isspace():
                self.last_significant = self.content[self.i]
            self.i += 1
        return self._finish()

    def _peek(self, offset: int = 1) -> str:
        position = self.i + offset
        return self.content[position] if position < self.n else ''

    def _step_comment(self) -> bool:
        ch = self.content[self.i]
        if self.in_line_comment:
            if ch == '\n':
                self.in_line_comment = False
            self.i += 1
            return True
        if self.in_block_comment:
            if ch == '*' and self._peek() == '/':
                self.in_block_comment = False
                self.i += 2
                return True
            self.i += 1
            return True
        return False

    def _step_string(self) -> bool:
        if not self.in_string:
            return False
        ch = self.content[self.i]
        if self.escaped:
            self.escaped = False
        elif ch == '\\':
            self.escaped = True
        elif ch == self.in_string:
            self.in_string = None
            self.last_significant = ch
        elif ch == '$' and self.in_string == '`' and self._peek() == '{':
            self.stack.append('${')                      # interpolation : du code jusqu'à son « } »
            self.in_string = None
            self.i += 2
            return True
        self.i += 1
        return True

    def _step_comment_start(self) -> bool:
        if self.content[self.i] != '/':
            return False
        following = self._peek()
        if following == '/':
            self.in_line_comment = True
        elif following == '*':
            self.in_block_comment = True
        else:
            return False
        self.word_buf = []
        self.i += 2
        return True

    def _step_regex(self) -> bool:
        """Saute un littéral regex (`/[{(]/g`) quand le « / » suit un symbole ou un mot-clé qui annonce une expression."""
        if self.content[self.i] != '/' or self.last_significant not in _REGEX_LITERAL_PRECEDERS:
            return False
        content, n = self.content, self.n
        j = self.i + 1
        in_char_class = False
        while j < n and content[j] != '\n':
            cj = content[j]
            if cj == '\\':
                j += 2
                continue
            if cj == '[':
                in_char_class = True
            elif cj == ']':
                in_char_class = False
            elif cj == '/' and not in_char_class:
                break
            j += 1
        if j < n and content[j] == '/':
            self.i = j + 1
            while self.i < n and content[self.i].isalpha():   # drapeaux (g, i, m…)
                self.i += 1
            self.last_significant = '/'
            self.word_buf = []
            return True
        # Pas de "/" fermant sur la même ligne : probablement pas un littéral regex (une regex ne s'étend jamais
        # sur plusieurs lignes), on retombe sur le traitement normal du caractère.
        return False

    def _step_code(self) -> bool:
        """Mot, guillemet ou délimiteur ; renvoie True quand l'analyse doit s'arrêter (trop de problèmes)."""
        ch = self.content[self.i]
        if ch.isalnum() or ch == '_':
            self.word_buf.append(ch)
            return False
        if self.word_buf:
            if ''.join(self.word_buf) in _REGEX_KEYWORD_PRECEDERS:
                # Comme un début d'expression : le "/" qui suivra (après d'éventuels espaces) doit être traité
                # comme un littéral regex.
                self.last_significant = ''
            self.word_buf = []
        if ch == "'" and self.i > 0 and (self.content[self.i - 1].isalnum() or self.content[self.i - 1] == '_'):
            return False                                 # apostrophe d'un mot (n'y, l'exécution) : pas une chaîne
        if ch in ('"', "'", '`'):
            self.in_string = ch
        elif ch in _OPENING:
            self.stack.append(ch)
        elif ch == '}' and self.stack and self.stack[-1] == '${':
            self.stack.pop()
            self.in_string = '`'
        elif ch in _PAIRS:
            return self._close(ch)
        return False

    def _close(self, ch: str) -> bool:
        if self.stack and self.stack[-1] == _PAIRS[ch]:
            self.stack.pop()
            return False
        line = self.content[:self.i].count('\n') + 1
        self.issues.append(f"'{ch}' inattendu ligne {line} (aucune ouverture correspondante).")
        return len(self.issues) >= _MAX_ISSUES

    def _finish(self) -> tuple[list[str], bool]:
        if self.in_string:
            self.issues.append(f"Chaîne de caractères non terminée (ouverte avec {self.in_string}).")
        if self.in_block_comment:
            self.issues.append("Commentaire /* ... */ non terminé.")
        for ch in self.stack:
            self.issues.append(f"'{ch}' jamais refermé.")
        truncated = self.in_block_comment or self.in_string == '`' or any(ch in ('{', '${') for ch in self.stack)
        return self.issues, truncated


def scan_delimiters(content: str) -> tuple[list[str], bool]:
    """Vérifie l'équilibre des accolades/parenthèses/crochets/guillemets d'un contenu.

    Heuristique volontairement simple (pas un vrai parseur JS/TS/JSX) : suffisante pour repérer les erreurs de
    génération les plus courantes (accolade ou parenthèse manquante en fin de fichier, chaîne non terminée), mais ne
    remplace pas une vraie compilation TypeScript ni un lint. Ignore le contenu des commentaires // et /* */ (une
    apostrophe de commentaire en français — « n'existe pas » — serait sinon lue comme une chaîne) ; reconnaît les
    littéraux regex (`/['"]/g`, aussi après `return`/`typeof`) ; lit les interpolations `${...}` des templates comme du
    code ; traite une apostrophe collée à un mot (« n'y », texte JSX) comme du texte. Limite connue : une regex qui ne
    suit ni un symbole ni un mot-clé de _REGEX_LITERAL_PRECEDERS / _REGEX_KEYWORD_PRECEDERS peut ne pas être reconnue.

    Renvoie (problèmes, tronqué). `tronqué` ne vaut vrai que pour la signature d'une coupure en fin de fichier : une
    accolade (ou `${`) jamais refermée, un template literal ou un commentaire /* */ non terminé. Les parenthèses et
    crochets isolés ne comptent pas : dans un texte JSX, « 1) » ou « (voir » sont des textes valides.
    """
    return _Scanner(content).scan()
