"""

    Reynir: Natural language processing for Icelandic

    Spelling and grammar checking module

    Copyright (C) 2020 Miðeind ehf.

        This program is free software: you can redistribute it and/or modify
        it under the terms of the GNU General Public License as published by
        the Free Software Foundation, either version 3 of the License, or
        (at your option) any later version.

        This program is distributed in the hope that it will be useful,
        but WITHOUT ANY WARRANTY; without even the implied warranty of
        MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
        GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.


    This module exposes functions to check spelling and grammar for
    text strings.

    It defines subclasses of the classes BIN_Token and Fast_Parser,
    both found in the Reynir package. These classes add error detection
    functionality to their base classes. After parsing a sentence, the
    resulting tree is navigated and nonterminals that are marked with
    $tag(error) in the CFG (Reynir.grammar) give rise to error annotations
    for their respective token spans.


    Error codes generated by this module:
    -------------------------------------

    E001: The sentence could not be parsed
    E002: A nonterminal tagged with 'error' is present in the parse tree
    E003: An impersonal verb occurs with an incorrect subject case
    E004: The sentence is probably not in Icelandic

"""

from threading import Lock

from reynir import Reynir, correct_spaces, TOK
from reynir.binparser import BIN_Token, BIN_Grammar
from reynir.fastparser import Fast_Parser, ParseForestNavigator
from reynir.reducer import Reducer
from reynir.settings import VerbSubjects
from reynir.matcher import SimpleTree

from .errtokenizer import tokenize as tokenize_and_correct


# The ratio of words in a sentence that must be found in BÍN
# for it to be analyzed as an Icelandic sentence
ICELANDIC_RATIO = 0.6


class Annotation:

    """ An annotation of a span of a token list for a sentence """

    def __init__(self, *, start, end, code, text, suggest=None, is_warning=False):
        assert isinstance(start, int)
        assert isinstance(end, int)
        self._start = start
        self._end = end
        if is_warning and not code.endswith("/w"):
            code += "/w"
        self._code = code
        self._text = text
        # If suggest is given, it is a suggested correction,
        # i.e. text that would replace the start..end token span.
        # The correction is in the form of token text joined by
        # " " spaces, so correct_spaces() should be applied to
        # it before displaying it.
        self._suggest = suggest

    def __str__(self):
        """ Return a string representation of this annotation """
        return "{0:03}-{1:03}: {2:6} {3}{4}".format(
            self._start, self._end, self._code, self._text,
            "" if self._suggest is None else " / [" + self._suggest + "]"
        )

    @property
    def start(self):
        """ The index of the first token to which the annotation applies """
        return self._start

    @property
    def end(self):
        """ The index of the last token to which the annotation applies """
        return self._end

    @property
    def code(self):
        """ A code for the annotation type, usually an error or warning code """
        return self._code

    @property
    def text(self):
        """ A description of the annotation """
        return self._text

    @property
    def suggest(self):
        """ A suggested correction for the token span """
        return self._suggest


class ErrorFinder(ParseForestNavigator):

    """ Utility class to find nonterminals in parse trees that are
        tagged as errors in the grammar, and terminals matching
        verb forms marked as errors """

    _CASE_NAMES = {"nf": "nefni", "þf": "þol", "þgf": "þágu", "ef": "eignar"}

    def __init__(self, ann, sent):
        super().__init__(visit_all=True)
        # Annotation list
        self._ann = ann
        # The original sentence object
        self._sent = sent
        # Token list
        self._tokens = sent.tokens
        # Terminal node list
        self._terminal_nodes = sent.terminal_nodes

    @staticmethod
    def _node_span(node):
        """ Return the start and end indices of the tokens
            spanned by the given node """
        first_token, last_token = node.token_span
        return (first_token.index, last_token.index)

    def _simple_tree(self, node):
        """ Return a SimpleTree instance spanning the deep tree
            of which node is the root """
        first, last = self._node_span(node)
        toklist = self._tokens[first : last + 1]
        return SimpleTree.from_deep_tree(node, toklist)

    def _node_text(self, node):
        """ Return the text within the span of the node """

        def text(t):
            """ If the token t is a word token, return a lower case
                version of its text, unless we have a reason to keep
                the original case, i.e. if it is a lemma that is upper case
                in BÍN """
            if t.kind != TOK.WORD:
                # Not a word token: keep the original text
                return t.txt
            if len(t.txt) > 1 and t.txt.isupper():
                # All uppercase: keep it that way
                return t.txt
            if t.val and any(m.stofn[0].isupper() for m in t.val):
                # There is an uppercase lemma for this word in BÍN:
                # keep the original form
                return t.txt
            # No uppercase lemma in BÍN: return a lower case copy
            return t.txt.lower()

        first, last = self._node_span(node)
        return correct_spaces(
            " ".join(text(t) for t in self._tokens[first : last + 1] if t.txt)
        )

    # Functions used to explain grammar errors associated with
    # nonterminals with error tags in the grammar

    def VillaHeldur(self, txt, variants, node):
        # 'heldur' er ofaukið
        return (
            "'{0}' er sennilega ofaukið".format(txt),
            ""
        )

    def VillaVístAð(self, txt, variants, node):
        # 'víst að' á sennilega að vera 'fyrst að'
        return (
            "'{0}' á sennilega að vera 'fyrst að'".format(txt),
            "fyrst að"
        )

    def VillaFráÞvíAð(self, txt, variants, node):
        # 'allt frá því' á sennilega að vera 'allt frá því að'
        return (
            "'{0}' á sennilega að vera '{0} að'".format(txt),
            "{0} að".format(txt)
        )

    def VillaAnnaðhvort(self, txt, variants, node):
        # Í stað 'annaðhvort' á sennilega að standa 'annað hvort'
        return (
            "Í stað '{0}' á sennilega að standa 'annað hvort'".format(txt),
            "annað hvort"
        )

    def VillaAnnaðHvort(self, txt, variants, node):
        # Í stað 'annað hvort' á sennilega að standa 'annaðhvort'
        return (
            "Í stað '{0}' á sennilega að standa 'annaðhvort'".format(txt),
            "annaðhvort"
        )

    def VillaFjöldiHluti(self, txt, variants, node):
        # Sögn sem á við 'fjöldi Evrópuríkja' á að vera í eintölu
        return "Sögn sem á við '{0}' á sennilega að vera í eintölu, ekki fleirtölu".format(txt)

    def VillaEinnAf(self, txt, variants, node):
        # Sögn sem á við 'einn af drengjunum' á að vera í eintölu
        return "Sögn sem á við '{0}' á sennilega að vera í eintölu, ekki fleirtölu".format(txt)

    def VillaSem(self, txt, variants, node):
        # 'sem' er sennilega ofaukið
        return (
            "'{0}' er að öllum líkindum ofaukið".format(txt),
            ""
        )

    def VillaAð(self, txt, variants, node):
        # 'að' er sennilega ofaukið
        return (
            "'{0}' er að öllum líkindum ofaukið".format(txt),
            ""
        )

    def VillaKomma(self, txt, variants, node):
        return (
            "Komma er líklega óþörf",
            ""
        )

    def VillaNé(self, txt, variants, node):
        return (
            "'né' gæti átt að vera 'eða'",
            "eða"
        )

    def VillaÞóAð(self, txt, variants, node):
        # [jafnvel] þó' á sennilega að vera '[jafnvel] þó að
        suggestion = "{0} að".format(txt)
        return (
            "'{0}' á sennilega að vera '{1}' (eða 'þótt')".format(txt, suggestion),
            suggestion
        )

    def VillaÍTölu(self, txt, variants, node):
        # Sögn á að vera í sömu tölu og frumlag
        children = list(node.enum_child_nodes())
        assert len(children) == 2
        subject = self._node_text(children[0])
        # verb_phrase = self._node_text(children[1])
        number = "eintölu" if "et" in variants else "fleirtölu"
        # Annotate the verb phrase
        start, end = self._node_span(children[1])
        return (
            "Sögn á sennilega að vera í {1} eins og frumlagið '{0}'".format(subject, number),
            start, end, None
        )

    def VillaFsMeðFallstjórn(self, txt, variants, node):
        # Forsetningin z á að stýra x-falli en ekki y-falli
        tnode = self._terminal_nodes[node.start]
        p = tnode.enclosing_tag("PP")
        subj = None
        if p is not None:
            try:
                subj = p.NP
            except AttributeError:
                pass
        if subj:
            cast_functions = {
                "nf": SimpleTree.nominative_np,
                "þf": SimpleTree.accusative_np,
                "þgf": SimpleTree.dative_np,
                "ef": SimpleTree.genitive_np
            }
            preposition = p.P.text
            suggestion = preposition + " " + cast_functions[variants].fget(subj)
            correct_np = correct_spaces(suggestion)
            return (
                "Á sennilega að vera '{2}' (forsetningin '{0}' stýrir {1}falli)."
                .format(
                    preposition,
                    ErrorFinder._CASE_NAMES[variants],
                    correct_np
                ),
                suggestion
            )
        # In this case, there's no suggested correction
        return (
            "Forsetningin '{0}' stýrir {1}falli."
            .format(
                txt.split()[0],
                ErrorFinder._CASE_NAMES[variants],
            )
        )

    def SvigaInnihaldNl(self, txt, variants, node):
        """ Explanatory noun phrase in a different case than the noun phrase
            that it explains """
        return (
            "'{0}' gæti átt að vera í {1}falli"
            .format(txt, ErrorFinder._CASE_NAMES[variants])
        )

    def VillaEndingIR(self, txt, variants, node):
        # 'læknirinn' á sennilega að vera 'lækninn'
        # In this case, we need the accusative form
        # of the token in self._tokens[node.start]
        tnode = self._terminal_nodes[node.start]
        suggestion = tnode.accusative_np
        correct_np = correct_spaces(suggestion)
        return (
            "Á sennilega að vera '{0}'"
            .format(correct_np),
            suggestion
        )

    def VillaEndingANA(self, txt, variants, node):
        # 'þingflokkana' á sennilega að vera 'þingflokkanna'
        # In this case, we need the genitive form
        # of the token in self._tokens[node.start]
        tnode = self._terminal_nodes[node.start]
        suggestion = tnode.genitive_np
        correct_np = correct_spaces(suggestion)
        return (
            "Á sennilega að vera '{0}'"
            .format(correct_np),
            suggestion
        )

    @staticmethod
    def find_verb_subject(tnode):
        """ Starting with a verb terminal node, attempt to find
            the verb's subject noun phrase """
        subj = None
        # First, check within the enclosing verb phrase
        # (the subject may be embedded within it, as in
        # ?'Í dag langaði Páli bróður að fara í sund')
        p = tnode.enclosing_tag("VP").enclosing_tag("VP")
        if p is not None:
            try:
                subj = p.NP_SUBJ
            except AttributeError:
                pass
        if subj is None:
            # If not found there, look within the
            # enclosing IP (inflected phrase) node, if any
            p = tnode.enclosing_tag("IP")
            if p is not None:
                # Found the inflected phrase:
                # find the NP-SUBJ node, if any
                try:
                    subj = p.NP_SUBJ
                except AttributeError:
                    pass
        return subj

    _CAST_FUNCTIONS = {
        "nf": SimpleTree.nominative_np,
        "þf": SimpleTree.accusative_np,
        "þgf": SimpleTree.dative_np,
        "ef": SimpleTree.genitive_np
    }

    def visit_token(self, level, node):
        """ Entering a terminal/token match node """

        terminal = node.terminal
        if terminal.category != "so":
            # Currently we only need to check verb terminals
            return

        tnode = self._terminal_nodes[node.start]
        verb = tnode.lemma

        def annotate_wrong_subject_case(subj_case_abbr, correct_case_abbr):
            wrong_case = self._CASE_NAMES[subj_case_abbr]
            # Retrieve the correct case
            correct_case = self._CASE_NAMES[correct_case_abbr]
            # Try to recover the verb's subject
            subj = self.find_verb_subject(tnode)
            code = "P_WRONG_CASE_" + subj_case_abbr + "_" + correct_case_abbr
            if subj is not None:
                # We know what the subject is: annotate it
                start, end = subj.span
                suggestion = self._CAST_FUNCTIONS[correct_case_abbr].fget(subj)
                correct_np = correct_spaces(suggestion)
                # Skip the annotation if it suggests the same text as the
                # original one; this can happen if the word forms for two
                # cases are identical
                if subj.tidy_text != correct_np:
                    self._ann.append(
                        Annotation(
                            start=start,
                            end=end,
                            code=code,
                            text="Á líklega að vera '{3}' (frumlag sagnarinnar 'að {0}' á að vera "
                                "í {1}falli en ekki í {2}falli)."
                                .format(verb, correct_case, wrong_case, correct_np),
                            suggest=suggestion
                        )
                    )
            else:
                # We don't seem to find the subject, so just annotate the verb.
                # In this case, there's no suggested correction.
                index = node.token.index
                self._ann.append(
                    Annotation(
                        start=index,
                        end=index,
                        code=code,
                        text="Frumlag sagnarinnar 'að {0}' á að vera "
                            "í {1}falli en ekki í {2}falli"
                            .format(verb, correct_case, wrong_case),
                    )
                )

        if not terminal.is_subj:
            # Check whether we had to match an impersonal verb
            # with this "normal" (non _subj) terminal
            # Check whether the verb is present in the VERBS_ERRORS
            # dictionary, with an 'nf' entry mapping to another case
            errors = VerbSubjects.VERBS_ERRORS.get(verb, set())
            if "nf" in errors:
                # We are using an impersonal verb as a normal verb,
                # i.e. with a subject in nominative case:
                # annotate an error
                annotate_wrong_subject_case("nf", errors["nf"])
            return

        # This is a so_subj terminal
        if not (terminal.is_op or terminal.is_sagnb or terminal.is_nh):
            return
        # This is a so_subj_op, so_subj_sagnb or so_subj_nh terminal
        # Check whether the associated verb is allowed
        # with a subject in this case
        # node points to a fastparser.Node instance
        # tnode points to a SimpleTree instance
        subj_case_abbr = terminal.variant(-1)  # so_1_þgf_subj_op_et_þf
        assert subj_case_abbr in {"nf", "þf", "þgf", "ef"}, (
            "Unknown case in " + terminal.name
        )
        # Check whether this verb has an entry in the VERBS_ERRORS
        # dictionary, and whether that entry then has an item for
        # the present subject case
        errors = VerbSubjects.VERBS_ERRORS.get(verb, set())
        if subj_case_abbr in errors:
            # Yes, this appears to be an erroneous subject case
            annotate_wrong_subject_case(subj_case_abbr, errors[subj_case_abbr])

    def visit_nonterminal(self, level, node):
        """ Entering a nonterminal node """
        if node.is_interior or node.nonterminal.is_optional:
            # Not an interesting node
            pass
        elif node.nonterminal.has_tag("error"):
            # This node has a nonterminal that is tagged with $tag(error)
            # in the grammar file (Reynir.grammar)
            suggestion = None
            start, end = self._node_span(node)
            span_text = self._node_text(node)
            # See if we have a custom text function for this
            # error-tagged nonterminal
            name = node.nonterminal.name
            variants = ""
            if "_" in name:
                # Separate the variants
                ix = name.index("_")
                variants = name[ix + 1:]
                name = name[:ix]
            # Find the text function by dynamic dispatch
            text_func = getattr(self, name, None)
            # The error code in this case is P_NT_ + the name of the error-tagged
            # nonterminal, however after cutting 'Villa' from its front
            code = "P_NT_" + (name[5:] if name.startswith("Villa") else name)
            if text_func is not None:
                # Yes: call it with the nonterminal's spanned text as argument
                ann = text_func(span_text, variants, node)
                if isinstance(ann, str):
                    ann_text = ann
                elif isinstance(ann, tuple):
                    if len(ann) == 2:
                        ann_text, suggestion = ann
                    else:
                        ann_text, start, end, suggestion = ann
                else:
                    assert False, "Text function {0} returns illegal type".format(name)
            else:
                # No: use a default text
                ann_text = (
                    "'{0}' er líklega rangt (regla {1})"
                    .format(span_text, node.nonterminal.name)
                )
            self._ann.append(
                # P_NT_ + nonterminal name: Probable grammatical error.
                Annotation(
                    start=start,
                    end=end,
                    code=code,
                    text=ann_text,
                    suggest=suggestion,
                    is_warning=code in {"P_NT_Að", "P_NT_Komma"}
                )
            )
        return None


class ErrorDetectionToken(BIN_Token):

    """ A subclass of BIN_Token that adds error detection behavior
        to the base class """

    _VERB_ERROR_SUBJECTS = VerbSubjects.VERBS_ERRORS

    def __init__(self, t, original_index):
        """ original_index is the index of this token in
            the original token list, as submitted to the parser,
            including not-understood tokens such as quotation marks """
        super().__init__(t, original_index)

    @staticmethod
    def verb_is_strictly_impersonal(verb, form):
        """ Return True if the given verb should not be allowed to match
            with a normal (non _op) verb terminal """
        if "OP" in form and not VerbSubjects.is_strictly_impersonal(verb):
            # We have a normal terminal, but an impersonal verb form. However,
            # that verb is not marked with an error correction from nominative
            # case to another case. We thus return True to prevent token-terminal
            # matching, since we don't have this specified as a verb error.
            return True
        # For normal terminals and impersonal verbs, we allow the match to
        # proceed if we have a specified error correction from a nominative
        # subject case to a different subject case.
        # Example: 'Tröllskessan dagaði uppi' where 'daga' is an impersonal verb
        # having a specified correction from nominative to accusative case.
        return False

    @staticmethod
    def verb_cannot_be_impersonal(verb, form):
        """ Return True if this verb cannot match an so_xxx_op terminal. """
        # We have a relaxed condition here because we want to catch
        # verbs being used impersonally that shouldn't be. So we don't
        # check for "OP" (impersonal) in the form, but we're not so relaxed
        # that we accept "BH" (imperative) or "NH" (infinitive) forms.
        return "BH" in form or "NH" in form

    # Variants that must be present in the verb form if they
    # are present in the terminal. We cut away the "op"
    # element of the tuple, since we want to allow impersonal
    # verbs to appear as normal verbs.
    _RESTRICTIVE_VARIANTS = ("sagnb", "lhþt", "bh")

    def verb_subject_matches(self, verb, subj):
        """ Returns True if the given subject type/case is allowed for this verb
            or if it is an erroneous subject which we can flag """
        return subj in self._VERB_SUBJECTS.get(
            verb, set()
        ) or subj in self._VERB_ERROR_SUBJECTS.get(verb, set())


class ErrorDetectingGrammar(BIN_Grammar):

    """ A subclass of BIN_Grammar that causes conditional sections in the
        Reynir.grammar file, demarcated using
        $if(include_errors)...$endif(include_errors),
        to be included in the grammar as it is read and parsed """

    def __init__(self):
        super().__init__()
        # Enable the 'include_errors' condition
        self.set_conditions({"include_errors"})


class ErrorDetectingParser(Fast_Parser):

    """ A subclass of Fast_Parser that modifies its behavior to
        include grammar error detection rules in the parsing process """

    _GRAMMAR_BINARY_FILE = Fast_Parser._GRAMMAR_FILE + ".error.bin"

    # Keep a separate grammar class instance and time stamp for
    # ErrorDetectingParser. This Python sleight-of-hand overrides
    # class attributes that are defined in BIN_Parser, see binparser.py.
    _grammar_ts = None
    _grammar = None
    _grammar_class = ErrorDetectingGrammar

    # Also keep separate class instances of the C grammar and its timestamp
    _c_grammar = None
    _c_grammar_ts = None

    @staticmethod
    def _create_wrapped_token(t, ix):
        """ Create an instance of a wrapped token """
        return ErrorDetectionToken(t, ix)


class ReynirCorrect(Reynir):

    """ Parser augmented with the ability to add spelling and grammar
        annotations to the returned sentences """

    # ReynirCorrect has its own class instances of a parser and a reducer,
    # separate from the Reynir class, as they use different settings and
    # parsing enviroments
    _parser = None
    _reducer = None
    _lock = Lock()

    def __init__(self):
        super().__init__()

    def tokenize(self, text):
        """ Use the correcting tokenizer instead of the normal one """
        return tokenize_and_correct(text)

    @property
    def parser(self):
        """ Override the parent class' construction of a parser instance """
        with self._lock:
            if (
                ReynirCorrect._parser is None
                or ReynirCorrect._parser.is_grammar_modified()[0]
            ):
                # Initialize a singleton instance of the parser and the reducer.
                # Both classes are re-entrant and thread safe.
                ReynirCorrect._parser = edp = ErrorDetectingParser()
                ReynirCorrect._reducer = Reducer(edp.grammar)
            return ReynirCorrect._parser

    @property
    def reducer(self):
        """ Return the reducer instance to be used """
        # Should always retrieve the parser attribute first
        assert ReynirCorrect._reducer is not None
        return ReynirCorrect._reducer

    @staticmethod
    def annotate(sent):
        """ Returns a list of annotations for a sentence object, containing
            spelling and grammar annotations of that sentence """
        ann = []
        words_in_bin = 0
        words_not_in_bin = 0
        # First, add token-level annotations
        for ix, t in enumerate(sent.tokens):
            if t.kind == TOK.WORD:
                if t.val:
                    # The word has at least one meaning
                    words_in_bin += 1
                else:
                    # The word has no recognized meaning
                    words_not_in_bin += 1
            # Note: these tokens and indices are the original tokens from
            # the submitted text, including ones that are not understood
            # by the parser, such as quotation marks and exotic punctuation
            if hasattr(t, "error_code") and t.error_code:
                ann.append(
                    Annotation(
                        start=ix,
                        end=ix + t.error_span - 1,
                        code=t.error_code,
                        text=t.error_description,
                    )
                )
        # Then, look at the whole sentence
        num_words = words_in_bin + words_not_in_bin
        if num_words > 2 and words_in_bin / num_words < ICELANDIC_RATIO:
            # The sentence contains less than 60% Icelandic
            # words: assume it's in a foreign language and discard the
            # token level annotations
            ann = [
                # E004: The sentence is probably not in Icelandic
                Annotation(
                    start=0,
                    end=len(sent.tokens) - 1,
                    code="E004",
                    text="Málsgreinin er sennilega ekki á íslensku",
                )
            ]
        elif sent.deep_tree is None:
            # If the sentence couldn't be parsed,
            # put an annotation on it as a whole.
            # In this case, we keep the token-level annotations.
            ann.append(
                # E001: Unable to parse sentence
                Annotation(
                    start=0,
                    end=len(sent.tokens) - 1,
                    code="E001",
                    text="Málsgreinin fellur ekki að reglum",
                )
            )
        else:
            # Successfully parsed:
            # Add error rules from the grammar
            ErrorFinder(ann, sent).go(sent.deep_tree)
        # Sort the annotations by their start token index,
        # and then by decreasing span length
        ann.sort(key=lambda a: (a.start, -a.end))
        return ann

    def create_sentence(self, job, s):
        """ Create a fresh sentence object and annotate it
            before returning it to the client """
        sent = super().create_sentence(job, s)
        # Add spelling and grammar annotations to the sentence
        sent.annotations = self.annotate(sent)
        return sent


def check_single(sentence):
    """ Check and annotate a single sentence, given in plain text """
    rc = ReynirCorrect()
    return rc.parse_single(sentence)


def check(text, *, split_paragraphs=False):
    """ Return a generator of checked paragraphs of text,
        each being a generator of checked sentences with
        annotations """
    rc = ReynirCorrect()
    # This is an asynchronous (on-demand) parse job
    job = rc.submit(text, parse=True, split_paragraphs=split_paragraphs)
    yield from job.paragraphs()


def check_with_custom_parser(text, *,
    split_paragraphs=False,
    parser_class=ReynirCorrect
):
    """ Return a dict containing parsed paragraphs as well as statistics,
        using the given correction/parser class. This is a low-level
        function; normally check_with_stats() should be used. """
    rc = parser_class()
    job = rc.submit(text, parse=True, split_paragraphs=split_paragraphs)
    # Enumerating through the job's paragraphs and sentences causes them
    # to be parsed and their statistics collected
    paragraphs = [[sent for sent in pg] for pg in job.paragraphs()]
    return dict(
        paragraphs=paragraphs,
        num_sentences=job.num_sentences,
        num_parsed=job.num_parsed,
        num_tokens=job.num_tokens,
        ambiguity=job.ambiguity,
        parse_time=job.parse_time,
    )


def check_with_stats(text, *, split_paragraphs=False):
    """ Return a dict containing parsed paragraphs as well as statistics """
    return check_with_custom_parser(text, split_paragraphs=split_paragraphs)
