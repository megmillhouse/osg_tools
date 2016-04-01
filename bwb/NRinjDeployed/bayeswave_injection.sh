#!/bin/bash

source ../etc/bayeswave_ldg-user-env.sh

which bayeswave
which bayeswave_post

catdir=/home/jclark308/lvc_nr/GaTech

bayeswave \
    --ifo H1 --H1-flow 16 --H1-cache LALSimAdLIGO \
    --H1-channel LALSimAdLIGO  \
    --inj GaTechIMBBH.xml --event 0 \
    --srate 512 --seglen 4 \
    --trigtime 1126621184 \
    --PSDstart 1126621184 --PSDlength 1024 \
    --NCmin 2 --NCmax 2 --dataseed 1234 \
    --inj-numreldata ${catdir}/GT0901.h5

bayeswave_post \
    --ifo H1 --H1-flow 16 --H1-cache LALSimAdLIGO \
    --H1-channel LALSimAdLIGO --trigtime 1126621184 \
    --srate 512 --seglen 4 --PSDstart 1126621184\
    --PSDlength 1024 --dataseed 1234 --0noise \
    --inj GaTechIMBBH.xml --event 0  \
    --inj-numreldata ${catdir}/GT0901.h5

# Make skymap
#python /home/jclark/src/lscsoft/bayeswave/trunk/postprocess/skymap/megasky.py

# Make the output page
#python /home/jclark/src/lscsoft/bayeswave/trunk/postprocess/megaplot.py