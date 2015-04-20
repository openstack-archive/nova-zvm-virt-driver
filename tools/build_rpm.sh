#! /bin/sh

export PBR_VERSION="2015.1"

if [ $# == 0 ]; then
    echo "You should provide project top dir."
    read TOP_DIR
fi

TOP_DIR=${TOP_DIR:-$1}
RELEASE=`date "+%Y%m%d"`

cd ${TOP_DIR}

rm -f ${TOP_DIR}/dist/*
python setup.py bdist_rpm --release ${RELEASE}
