#!/bin/sh

MAX_LINE_LENGTH=120

prog_name=$(basename $0)

sub_help(){
    echo "Usage: $prog_name subcommand [filename ...]\n"
    echo "Subcommands:"
    echo "    format                Format the code"
    echo "    check_format          Verify formatting without making any changes"
    echo "    check_types           Verify typing"
    echo "    verify                Run all checks"
    echo ""
}

sub_check_format() {
    ruff check $@ && ruff format --check $@
}

sub_format(){
    ruff check --fix $@ && ruff format $@
}

sub_check_types(){
    python3 -m pyright
}

sub_verify(){
    sub_check_format $@ && sub_check_types
}

subcommand=$1
case $subcommand in
    "" | "-h" | "--help")
        sub_help
        ;;
    *)
        shift

        FILES=${@:-"."}
        sub_${subcommand} $FILES
        RETVAL=$?

        if [ $RETVAL = 127 ]; then
            echo "Error: '$subcommand' is not a known subcommand." >&2
            echo "       Run '$prog_name --help' for a list of known subcommands." >&2
        fi

        ;;
esac

RETVAL=${RETVAL:=$?}
exit $RETVAL
