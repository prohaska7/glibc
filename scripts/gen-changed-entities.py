#!/usr/bin/python3
import subprocess
import sys
import os
import re

#------------------------------------------------------------------------------
# C Parser.
#------------------------------------------------------------------------------
from enum import Enum
debug = False

class block_type(Enum):
    file = 1
    macro_cond = 2
    macro_def = 3
    macro_undef = 4
    macro_include = 5
    macro_info = 6
    decl = 7
    func = 8
    composite = 9
    macrocall = 10
    fndecl = 11
    assign = 12


def collapse_macros(op):
    # Consolidate macro defs across multiple lines.
    new_op = []
    cur_line = ''
    join_line = False
    for l in op:
        if join_line:
            cur_line = cur_line[:-1] + ' ' + l
        else:
            cur_line = l

        if cur_line[0] == '#' and cur_line[-1] == '\\':
            join_line = True
            continue
        else:
            join_line = False

        new_op.append(cur_line)

    return new_op


def remove_comments(op):
    new_op = []

    # The simpler one-line comments.
    for l in op:
        # FIXME: This assumes that there's always only one comment per line.
        rem = re.sub(r'/\*.*\*/', r'', l)
        if rem:
            new_op.append(rem.strip())

    op = new_op
    new_op = []

    in_comment = False
    for l in op:
        if in_comment:
            loc = l.find('*/')
            if loc == -1:
                continue
            else:
                in_comment = False
                rem = l[loc + 2:]
                if rem:
                    new_op.append(rem)
        else:
            loc = l.find('/*')
            if loc == -1:
                new_op.append(l)
            else:
                in_comment = True
                rem = l[:loc]
                if rem:
                    new_op.append(rem)

    return new_op


# Parse macros.
def parse_macro(op, loc, code, start = '', else_start = ''):
    cur = op[loc]
    loc = loc + 1
    endblock = False

    debug_print('PARSE_MACRO: %s' % cur)

    # Collapse the macro into a single line.
    while cur[-1] == '\\':
        cur = cur[:-1] + ' ' + op[loc]
        loc = loc + 1


    # Remove the # and strip spaces again.
    cur = cur[1:].strip()

    # Include file.
    if cur.find('include') == 0:
        m = re.search(r'include\s*["<]?([^">]+)[">]?', cur)
        include = {}
        include['name'] = m.group(1)
        include['type'] = block_type.macro_include
        include['contents'] = [cur]
        include['parent'] = code
        code['contents'].append(include)

    # Macro definition.
    if cur.find('define') == 0:
        m = re.search(r'define\s+([a-zA-Z0-9_]+)', cur)
        macrodef = {}
        macrodef['name'] = m.group(1)
        macrodef['type'] = block_type.macro_def
        macrodef['contents'] = [cur]
        macrodef['parent'] = code
        code['contents'].append(macrodef)

    if cur.find('undef') == 0:
        m = re.search(r'undef\s+([a-zA-Z0-9_]+)', cur)
        macrodef = {}
        macrodef['name'] = m.group(1)
        macrodef['type'] = block_type.macro_undef
        macrodef['contents'] = [cur]
        macrodef['parent'] = code
        code['contents'].append(macrodef)

    # Macro definition.
    if cur.find('error') == 0 or cur.find('warning') == 0:
        m = re.search(r'(error|warning)\s+"?(.*)"?', cur)
        if m:
            name = m.group(2)
        else:
            name = '<blank>'
        macrodef = {}
        macrodef['name'] = name
        macrodef['type'] = block_type.macro_info
        macrodef['contents'] = [cur]
        macrodef['parent'] = code
        code['contents'].append(macrodef)

    # Start of an #if or #ifdef block.
    elif cur.find('if') == 0:
        rem = re.sub(r'ifndef', r'!', cur).strip()
        rem = re.sub(r'(ifdef|defined|if)', r'', rem).strip()
        ifdef = {}
        ifdef['name'] = rem
        ifdef['type'] = block_type.macro_cond
        ifdef['contents'] = []
        ifdef['parent'] = code
        code['contents'].append(ifdef)
        loc = parse(op, loc, ifdef, start)

    # End the previous #if/#elif and begin a new block.
    elif cur.find('elif') == 0 and code['parent']:
        rem = re.sub(r'(elif|defined)', r'', cur).strip()
        ifdef = {}
        ifdef['name'] = rem
        ifdef['type'] = block_type.macro_cond
        ifdef['contents'] = []
        ifdef['parent'] = code['parent']
        # Here's the key thing: The #else block should go into the current
        # block's parent.
        code['parent']['contents'].append(ifdef)
        loc = parse(op, loc, ifdef, else_start)
        endblock = True

    # End the previous #if/#elif and begin a new block.
    elif cur.find('else') == 0 and code['parent']:
        ifdef = {}
        ifdef['name'] = '!(' + code['name'] + ')'
        ifdef['type'] = block_type.macro_cond
        ifdef['contents'] = []
        ifdef['parent'] = code['parent']
        code['parent']['contents'].append(ifdef)
        loc = parse(op, loc, ifdef, else_start)
        endblock = True

    elif cur.find('endif') == 0 and code['parent']:
        endblock = True

    return (loc, endblock)


# Given the start of a scope CUR, lap up all code up to the end of scope
# indicated by the closing brace.
def fast_forward_scope(cur, op, loc, open='{', close='}'):
    nesting = cur.count(open) - cur.count(close)
    while nesting > 0 and loc < len(op):
        cur = cur + ' ' + op[loc]

        nesting = nesting + op[loc].count(open)
        nesting = nesting - op[loc].count(close)
        loc = loc + 1

    return (cur, loc)


# Different types of declarations.
def parse_decl(name, cur, op, loc, code, blocktype):
    debug_print('FOUND DECL: %s' % name)
    block = {}
    block['name'] = name
    block['type'] = blocktype
    block['contents'] = [cur]
    block['parent'] = code
    code['contents'].append(block)

    return loc


# Assignments.
def parse_assign(name, cur, op, loc, code):
    debug_print('FOUND ASSIGN: %s' % name)
    # Lap up everything up to semicolon.
    while ';' not in cur and loc < len(op):
        cur = op[loc]
        loc = loc + 1

    block = {}
    block['name'] = name
    block['type'] = block_type.assign
    block['contents'] = [cur]
    block['parent'] = code
    code['contents'].append(block)

    return loc


# Structs or unions.
def parse_composite(name, cur, op, loc, code):
    if not name:
        name = '<anonymous>'

    # Lap up all of the struct definition.
    (cur, loc) = fast_forward_scope(cur, op, loc)

    block = {}
    block['name'] = name
    block['type'] = block_type.composite
    block['contents'] = [cur]
    block['parent'] = code
    code['contents'].append(block)

    return loc


# Parse a function.  NAME is the function name.
def parse_func(name, cur, op, loc, code):
    debug_print('FOUND FUNC: %s' % name)

    # Consume everything up to the ending brace of the function.
    (cur, loc) = fast_forward_scope(cur, op, loc)

    block = {}
    block['name'] = name
    block['type'] = block_type.func
    block['contents'] = [cur]
    block['parent'] = code
    code['contents'].append(block)

    return loc


# Parse a function.  NAME is the function name.
def parse_macrocall(name, cur, op, loc, code):
    debug_print('FOUND MACROCALL: %s' % name)

    block = {}
    block['name'] = name
    block['type'] = block_type.macrocall
    block['contents'] = [cur]
    block['parent'] = code
    code['contents'].append(block)

    return loc


def parse_c_expr(cur, op, loc, code, start):
    debug_print('PARSING: %s' % cur)

    ATTRIBUTE = \
        r'((_*(attribute|ATTRIBUTE)_*(\s*\(\([^)]+\)\)|\w+))|weak_function)';
    #ARGLIST = r'[\w\s\*]+' + ATTRIBUTE + '?,?\s*'
    ARGLIST = r'(\w+[\s\*]+\w+' + ATTRIBUTE + '?,?\s*)|void'

    # Regular expressions.
    #
    # Function or a macro call that doesn't need a semicolon: foo (args, ...)
    # We later distinguish between the two by peeking into the next line.
    func_re = re.compile(ATTRIBUTE + r'*\s*(\w+)\s*\((' + ARGLIST + ')*\)\s*{')
    macrocall_re = re.compile(r'(\w+)\s*\(\w+(\s*,\s*[\w\.]+)*\)$')
    # Composite types such as structs and unions.
    composite_re = re.compile(r'(struct|union|enum)\s*(\w*)\s*{')
    # Static assignments.
    assign_re = re.compile(r'(\w+)\s*(\[\])?\s*([^\s]*attribute[\s\w()]+)?\s*=')
    # Function Declarations. FIXME BROKEN
    fndecl_re = re.compile(r'(\w+)\s*\([^;]+\)\s*' + ATTRIBUTE + '*;')
    # Function pointer typedefs.
    typedef_fn_re = re.compile(r'\(\*(\w+)\)\s*\([^)]+\);')
    # Simple decls.
    decl_re = re.compile(r'(\w+)(\[\w+\])?\s*' + ATTRIBUTE + '?;')

    # Composite type declarations.
    found = re.search(composite_re, cur)
    if found:
        return found, parse_composite(found.group(2), cur, op, loc, code)

    # Assignments.  This should cover struct and array assignments too.
    found = re.search(assign_re, cur)
    if found:
        return found, parse_assign(found.group(1), cur, op, loc, code)

    # Typedefs.
    found = re.search(typedef_fn_re, cur)
    if found:
        return found, parse_decl(found.group(1), cur, op, loc, code,
                block_type.decl)

    # Function declarations are pretty straightforward compared to function
    # definitions, which have to account for any __attribute__ annotations
    # for its arguments.  With declarations, we just match the last closing
    # bracket and the semicolon following it.
    found = re.search(fndecl_re, cur)
    if found:
        return found, parse_decl(found.group(1), cur, op, loc, code,
                block_type.fndecl)

    # Functions or macro calls that don't end with a semicolon.
    found = re.search(func_re, cur)
    if found:
        return found, parse_func(found.group(5), cur, op, loc, code)

    # Functions or macro calls that don't end with a semicolon.  We need to peek
    # ahead to make sure that we don't mis-identify a function.  This happens
    # only with functions that take no arguments.
    found = re.search(macrocall_re, cur)
    if found and (loc >= len(op) or '{' not in op[loc]):
        return found, parse_macrocall(found.group(1), cur, op, loc, code)

    # Finally, all declarations.
    found = re.search(decl_re, cur)
    if found:
        return found, parse_decl(found.group(1), cur, op, loc, code,
                block_type.decl)

    return found, loc


# Parse the file line by line.  The function assumes a mostly GNU coding
# standard compliant input so it might barf with anything that is eligible for
# the Obfuscated C code contest.
#
# The basic idea of the parser is to identify macro conditional scopes and
# definitions, includes, etc. and then parse the remaining C code in the context
# of those macro scopes.  The parser does not try to understand the semantics of
# the code or even validate its syntax.  It only records high level symbols in
# the source and makes a tree structure to indicate the declaration/definition
# of those symbols and their scope in the macro definitions.
#
# LOC is the first unparsed line.
def parse(op, loc, code, start = ''):
    cur = start
    endblock = False

    while loc < len(op):
        nextline = op[loc].strip()

        if not nextline:
            loc = loc + 1
            continue

        # Macros.
        if nextline[0] == '#':
            (loc, endblock) = parse_macro(op, loc, code, cur, start)
            if endblock and not cur:
                return loc
        # Rest of C Code.
        else:
            cur = cur + ' ' + nextline
            found, loc = parse_c_expr(cur, op, loc + 1, code, cur)
            if found:
                cur = ''
            if endblock:
                return loc

    return loc


def print_tree(tree, indent):
    if tree['type'] == block_type.macro_cond or tree['type'] == block_type.file:
        print('%sScope: %s' % (' ' * indent, tree['name']))
        for c in tree['contents']:
            print_tree(c, indent + 4)
        print('%sEndScope: %s' % (' ' * indent, tree['name']))
    else:
        if tree['type'] == block_type.func:
            print('%sFUNC: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.composite:
            print('%sCOMPOSITE: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.assign:
            print('%sASSIGN: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.fndecl:
            print('%sFNDECL: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.decl:
            print('%sDECL: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.macrocall:
            print('%sMACROCALL: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.macro_def:
            print('%sDEFINE: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.macro_include:
            print('%sINCLUDE: %s' % (' ' * indent, tree['name']))
        elif tree['type'] == block_type.macro_undef:
            print('%sUNDEF: %s' % (' ' * indent, tree['name']))
        else:
            print('%sMACRO LEAF: %s' % (' ' * indent, tree['name']))

#------------------------------------------------------------------------------


def debug_print(*args, **kwargs):
    if debug:
        print(*args, file=sys.stderr, **kwargs)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def usage(name):
    eprint("usage: %s <from-ref> <to-ref>" % name)
    sys.exit(os.EX_USAGE)

def decode(string):
    codecs = ['utf8', 'latin1', 'cp1252']

    for i in codecs:
        try:
            return string.decode(i)
        except UnicodeDecodeError:
            pass

    eprint('Failed to decode: %s' % string)

def cleaned(ip):
    # Returns the output from a command after cleaning it up, i.e. removing
    # trailing spaces, newlines and dropping blank lines.
    op = list(filter(None, [decode(x[:-1]).strip() for x in ip]))
    return op

def exec_git_cmd(args):
    args.insert(0, 'git')
    print(args)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE)

    return cleaned(list(proc.stdout))


def list_commits(revs):
    ref = revs[0] + '..' + revs[1]
    return exec_git_cmd(['log', '--pretty=%H', ref])

def analyze_diff(oldfile, newfile, filename):
    # Ignore non-C files.
    if filename.find('.c') < 0 and filename.find('.h') < 0:
        return

    print('\t<List diff between oldfile and newfile>')

    left = parse_output(exec_git_cmd(['show', oldfile]))
    right = parse_output(exec_git_cmd(['show', newfile]))

    print('LEFT TREE')
    print('-' * 80)
    print_tree(left, 0)
    print('RIGHT TREE')
    print('-' * 80)
    print_tree(right, 0)


def parse_output(op):
    tree = {}
    tree['name'] = ''
    tree['type'] = block_type.file
    tree['contents'] = []
    tree['parent'] = None
    #op = preprocess(op, right)
    op = remove_comments(op)
    op = parse(op, 0, tree)

    return tree


def list_changes(commit):
    op = exec_git_cmd(['show', '--date=short', '--raw', commit])
    author = ''
    date = ''
    merge = False

    for l in op:
        if l.find('Author:') == 0:
            tmp=l[7:].split('<')
            authorname = tmp[0].strip()
            authoremail=tmp[1][:-1].strip()
        elif l.find('Date:') == 0:
            date=l[5:].strip()
        elif l.find('Merge:') == 0:
            merge = True

        # We got Author and Date, so don't bother with the remaining output.
        if author != '' and date != '':
            break

    # Find raw commit information for all non-ChangeLog files.
    op = [x[1:] for x in op
            if len(x) > 0 and x[0] == ':' and x.find('ChangeLog') == -1]

    # It was only the ChangeLog, ignore.
    if len(op) == 0:
        return

    print('%s  %s  <%s>\n' % (date, authorname, authoremail))

    if merge:
       print('\t MERGE COMMIT: %s\n' % commit)
       return

    print('\tCOMMIT: %s' % commit)

    # Each of these lines has a space separated format like so:
    # :<OLD MODE> <NEW MODE> <OLD REF> <NEW REF> <OPERATION> <FILE1> <FILE2>
    #
    # where OPERATION can be one of the following:
    # A: File added
    # D: File removed
    # M: File modified
    # R[0-9]{3}: File renamed, with the 3 digit number following it indicating
    # what percentage of the file is intact.
    #
    # FILE2 is set only when OPERATION is R, to indicate the new file name.
    #
    # Also note that merge commits have a different format here, with three
    # entries each for the modes and refs, but we don't bother with it for now.
    for f in op:
        data = f.split()
        if data[4] == 'A':
            print('\t* %s: New file.' % data[5])
        elif data[4] == 'D':
            print('\t* %s: Delete file.' % data[5])
        elif data[4] == 'M':
            print('\t* %s: Modified.' % data[5])
            analyze_diff(data[2], data[3], data[5])
        elif data[4][0] == 'R':
            change = int(data[4][1:])
            print('\t* %s: Move to...' % data[5])
            print('\t* %s: ... here.' % data[6])
            if change < 100:
                analyze_diff(data[2], data[3], data[6])
        else:
            eprint('%s: Unknown line format %s' % (commit, data[4]))
            sys.exit(42)

    print('')


def main(revs):
    commits = list_commits(revs)
    for commit in commits:
        list_changes(commit)


def parser_file_test(f):
    with open(f) as srcfile:
        op = srcfile.readlines()
        op = [x[:-1] for x in op]
        tree = parse_output(op)
        print_tree(tree, 0)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        usage(sys.argv[0])

    if sys.argv[1] == '-t':
        debug = True
        parser_file_test(sys.argv[2])
    else:
        main(sys.argv[1:])
