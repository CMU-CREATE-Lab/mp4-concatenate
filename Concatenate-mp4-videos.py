#!/usr/bin/python

# Append to MP4 video in-place
# -------------------------------
# 
# Append to an mp4 video, modifying the original video in-place to
# minimize I/O.  Created specifically for [Time
# Machine](http://timemachine.cmucreatelab.org/wiki/Main_Page).
# Ignores audio, and only tested on videos created by ffmpeg by Time
# Machine.
# 
# For the purpose of this tool, mp4 videos have 4 sections:
# - header ("ftyp" section)
# - movie information ("moov" section), which contains lots of metadata and indexes
# - free space ("free" section)
# - mp4-compressed video frames, concatenated into "chunks", which are then concatenated into the "mdat" section
# 
# This tool concatenates videos at granularity of "chunks", meaning if
# a video is composed of multiple chunks, those chunks can be
# independently selected for putting into the resulting video.
# 
# If you consider the append operation A += B, A will be modified in
# place to include some or all of B at the end.  Not all of the chunks
# from A or B are required to be in the final video.  But any chunks
# removed from A must be removed from the end of the video, so that
# the original frames remaining in A start at time=0 and will not need
# to be moved in the file.
# 
# As the video A grows through successive append operations, the
# indexes in the "moov" atom will grow.  To prevent needing to
# relocate the potentially quite large "mdat" section, we use a "free"
# section which we can shrink in-place as "mdat" grows.  But if the
# "mdat" grows too large and exhausts the free space, A will need to
# be completely rewritten, with "mdat" moving.  This is very likely to
# happen the first time you append to A, since A probably won't have
# originally been created with a free section of significant size.
# (And sometimes A will be created with the "moov" section after the
# "mdat", which reduces streaming efficiency -- see discussions around
# the "qtfaststart" tool).  So expect A to be rewritten on the first
# append.
# 
# When A is rewritten to include more free space, it's useful to know
# if there will be more appends in the future, and if so, much free
# space should be included now to allow for those future appends to
# not require rewriting to move "mdat".  You can specify a number of
# frames, in which case the additional free space will be created to
# allow roughly that number of frames to be appended before needing to
# rewrite the video.  However, when rewriting, the tool will refuse to
# create a free area smaller thant he current "mdat" area, meaning
# there should be at least enough space to double the video size.  So
# worst case if you chronically estimate too low, the video will be
# rewritten log(n) times over the long haul.
# 

# Reference:  https://developer.apple.com/library/mac/documentation/QuickTime/QTFF/QTFFChap2/qtff2.html#//apple_ref/doc/uid/TP40000939-CH204-56313
#         

from mp4lib import *

# In[3]:

def parse_filename_and_chunks(filename, writable=False):
    match = re.match(r'(.*)(\[(-?\d+)?\:(-?\d+)?\])', filename)
    if match:
        filename = match.groups()[0]
        groups = match.groups()[1]
    else:
        groups = ''

    chunks = MP4(filename, writable=writable).chunks()
    chunks = eval('chunks' + groups)
    return chunks

# In[8]:

def dump_frames(filename_and_chunk):
    for chunk in parse_filename_and_chunks(filename_and_chunk, writable=False):
        chunk.dump()

def append(filenames_and_chunks, future_frames=1000):
    while True:
        chunks = parse_filename_and_chunks(filenames_and_chunks[0], writable=True)

        for file in filenames_and_chunks[1:]:
            chunks.extend(parse_filename_and_chunks(file))

        dest = chunks[0].video

        try:
            dest.update_in_place_using_chunks(chunks)
        except NeedsRewriteException as e:
            # Assume approx 6 bytes per frame
            padding = max(future_frames * 6, dest.info['moov']['atomsize'])
            free = e.space_needed + padding
            print 'rewriting video with free=%d' % free
            tmpname = '%s-tmp%d' % (dest.filename, os.getpid())
            dest.copy_with_padding(tmpname, free)
            os.rename(tmpname, dest.filename)
            continue
        break

# In[10]:

# In[45]:

# How to concatenate two videos using ffmpeg

#!ffmpeg -i short.mp4 -c copy -bsf:v h264_mp4toannexb -f mpegts -y short.ts
#!ffmpeg -i "concat:short.ts|short.ts" -c copy -y combined.mp4
#!ls -l short.mp4 combined.mp4

import argparse

def main():
    parser = argparse.ArgumentParser(description='Append videos in-place')
    parser.add_argument('filenames_and_chunks', metavar='N', nargs='+',
                        help='an integer for the accumulator')
    parser.add_argument('--future_frames', default=1000,
                        help='Specify number of frames for future appending (to better estimate freespace)')
    parser.add_argument('--dump_frames', action='store_true')
    
    args = parser.parse_args()
    if args.dump_frames:
        if len(args.filenames_and_chunks) != 1:
            raise Exception('Must have one video for --dump_frames')
        dump_frames(args.filenames_and_chunks[0])
    else:
        append(args.filenames_and_chunks, int(args.future_frames))

if __name__ == "__main__":
    main()

