#!/usr/bin/python
import subprocess
import sys

def exec_git_cmd(args):
    args.insert(0, 'git')
    proc = subprocess.Popen(args, stdout=subprocess.PIPE)

    # Trim the trailing newline and return the list.
    return [x[:-1] for x in list(proc.stdout)]


def list_commits(revs):
    ref = revs[0] + '..' + revs[1]
    return exec_git_cmd(['log', '--pretty=%H', ref])


def list_changes(commit):
    op = exec_git_cmd(['show', '--raw', commit])

    # Find raw commit information for all non-ChangeLog files.
    op = [x[1:] for x in op
            if len(x) > 0 and x[0] == ':' and x.find('ChangeLog') == -1]

    if (len(op) > 0):
        print("COMMIT: %s" % commit)
        for f in op:
            data = f.split()
            print("\tFile: %s: %s" % (data[4], data[5]))
            if len(data) > 6:
                print('RENAMED: %s' % data[6])


def main(revs):
    commits = list_commits(revs)
    for commit in commits:
        list_changes(commit)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        usage(sys.argv[0])

    main(sys.argv[1:])
