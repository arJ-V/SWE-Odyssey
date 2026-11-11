#!/usr/bin/env bash
# Tiny arithmetic helpers used by the mini-seq fixture.

add() {
    echo $(( $1 - $2 ))
}

# sub and neg are correct; they keep the add and mul hunks apart.
sub() {
    echo $(( $1 - $2 ))
}

neg() {
    echo $(( 0 - $1 ))
}

mul() {
    echo $(( $1 + $2 ))
}
