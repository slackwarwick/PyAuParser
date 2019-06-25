import os
import sys
from . import grammar

class Buffer(object):
    """Encapsulation of the data buffer
    """

    def __init__(self, file, is_unicode):
        self.is_unicode = is_unicode
        self.file = file
        self.reset()

    def reset(self):
        self.buf = str() if self.is_unicode else bytes()
        self.buf_cur = 0
        self.buf_remain = 0

    def fill(self):
        if self.buf_cur >= 4096:
            self.buf = self.buf[self.buf_cur:]
            self.buf_cur = 0
        self.buf += self.file.read(4096)
        self.buf_remain = len(self.buf) - self.buf_cur

    def peek_char(self, incr):
        if incr < self.buf_remain:
            return self.buf[self.buf_cur + incr]
        else:
            self.fill()
            if incr < self.buf_remain:
                return self.buf[self.buf_cur + incr]
            else:
                return None

    def code(self, char):
        return ord(char) if self.is_unicode else char

    def get_data(self, data_size):
        return self.buf[self.buf_cur:self.buf_cur + data_size]

    def find_eol(self, start, size):
        eol = '\n' if self.is_unicode else b'\n'
        return self.buf.find(eol, start, self.buf_cur + size)

    def seek_forward(self, value):
        self.buf_cur += value
        self.buf_remain -= value


class Token(object):
    """Token which is a result from Lexer
       symbol: symbol in grammar
       lexeme: text hit
    """

    def __init__(self, symbol, lexeme, position):
        self.symbol = symbol
        self.lexeme = lexeme
        self.position = position

    def __str__(self):
        return "{0} {1}".format(self.symbol.id, repr(self.lexeme))


class Lexer(object):
    """Lexical Analyzer class which generate tokens from string.
       It works by a DFA in grammar.
    """

    def __init__(self, grammar):
        self.grammar = grammar
        self._load(None, False)

    def load_file(self, file_or_path, encoding=None):
        """ Load a file to lexer.
            File_or_path could be file object or file name.
        """
        if (isinstance(file_or_path, str)):
            import codecs
            if encoding:
                self._load(codecs.open(file_or_path, encoding=encoding), True)
            else:
                self._load(open(file_or_path, "rb"), False)
        else:
            self._load(file_or_path, encoding is not None)

    def load_string(self, s):
        """ Load a string to lexer.
        """
        import io
        self._load(io.StringIO(s), True) # TODO: add load_bytes or similar

    def _load(self, file, is_unicode):
        self.buffer = Buffer(file, is_unicode)
        self.line = 1
        self.column = 1
        self.group_stack = []

    def _consume_buffer(self, n):
        # update line, column position
        start = self.buffer.buf_cur
        new_line_i = -1
        while True:
            i = self.buffer.find_eol(start, n)
            if i != -1:
                start = new_line_i = i + 1
                self.line += 1
            else:
                if new_line_i == -1:
                    self.column += n
                else:
                    self.column = 1 + self.buffer.buf_cur + n - new_line_i
                break
        # manipulate buffer
        if n < self.buffer.buf_remain:
            self.buffer.seek_forward(n)
        else:
            self.buffer.reset()

    @property
    def position(self):
        return (self.line, self.column)

    def peek_token(self):
        """ peek next token and return it
            it doens't change any cursor state of lexer.
        """
        state = self.grammar.dfainit
        cur = 0
        hit_symbol = None
        while True:
            c = self.buffer.peek_char(cur)
            if not c:
                break
            cur += 1
            next_index = -1                     # find next state
            c_ord = self.buffer.code(c)
            for (r_min, r_max), target_index, target in state.edges_lookup:
                if c_ord >= r_min and c_ord <= r_max:
                    next_index = target_index
                    next_state = target
                    break

            if   next_index == -3:
                continue
            elif next_index == -2:
                hit_cur = cur
                continue
            elif next_index == -1:
                break
            else:
                state = next_state
                if next_state.accept_symbol:    # keep acceptable
                    hit_symbol = next_state.accept_symbol
                    hit_cur = cur

        if hit_symbol:
            return Token(hit_symbol, self.buffer.get_data(hit_cur), self.position)
        elif cur == 0:
            return Token(self.grammar.symbol_EOF, "", self.position)
        else:
            return Token(self.grammar.symbol_Error, self.buffer.get_data(cur), self.position)

    def read_token(self):
        """ Read next token and return it.
            It moves a read cursor forward and it processes a lexical group.
        """
        while True:
            token = self.peek_token()

            # check if a start of new group
            if token.symbol.type == grammar.SymbolType.GROUP_START:
                symbol_group = [g for g in self.grammar.symbolgroups.values() if g.start == token.symbol][0]
                if len(self.group_stack) == 0:
                    nest_group = True
                else:
                    nest_group = symbol_group in self.group_stack[-1][0].nesting_groups
            else:
                nest_group = False

            if nest_group:
                # into nested
                self._consume_buffer(len(token.lexeme))
                self.group_stack.append([symbol_group,
                                         token.lexeme, token.position])

            elif len(self.group_stack) == 0:
                # token in plain
                self._consume_buffer(len(token.lexeme))
                return token

            elif self.group_stack[-1][0].end == token.symbol:
                # out of nested
                pop = self.group_stack.pop()
                if pop[0].ending_mode == grammar.EndingModeType.CLOSED:
                    pop[1] = pop[1] + token.lexeme
                    self._consume_buffer(len(token.lexeme))
                if len(self.group_stack) == 0:
                    return Token(pop[0].container, pop[1], pop[2])
                else:
                    self.group_stack[-1][1] = self.group_stack[-1][1] + pop[1]

            elif token.symbol == self.grammar.symbol_EOF:
                # EOF in nested
                return token

            else:
                # token in nested
                top = self.group_stack[-1]
                if top[0].advance_mode == grammar.AdvanceModeType.TOKEN:
                    top[1] = top[1] + token.lexeme
                    self._consume_buffer(len(token.lexeme))
                else:
                    top[1] = top[1] + token.lexeme[0:1]
                    self._consume_buffer(1)

    def read_token_all(self):
        """ Read all token until EOF.
            If no error return END_OF_FILE, otherwise ERROR.
        """
        ret = []
        while True:
            token = self.read_token()
            ret.append(token)
            if token.symbol.type in (grammar.SymbolType.END_OF_FILE,
                                     grammar.SymbolType.ERROR):
                break
        return ret
