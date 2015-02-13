# mp4-concatenate
Efficiently concatenate MP4 videos in-place, for Time Machine

Append to MP4 video in-place
-------------------------------

Append to an mp4 video, modifying the original video in-place to minimize I/O.  Created specifically for [Time Machine](http://timemachine.cmucreatelab.org/wiki/Main_Page).  Ignores audio, and only tested on videos created by ffmpeg by Time Machine.

For the purpose of this tool, mp4 videos have 4 sections:
- header ("ftyp" section)
- movie information ("moov" section), which contains lots of metadata and indexes
- free space ("free" section)
- mp4-compressed video frames, concatenated into "chunks", which are then concatenated into the "mdat" section

This tool concatenates videos at granularity of "chunks", meaning if a video is composed of multiple chunks, those chunks can be independently selected for putting into the resulting video.

If you consider the append operation A += B, A will be modified in place to include some or all of B at the end.  Not all of the chunks from A or B are required to be in the final video.  But any chunks removed from A must be removed from the end of the video, so that the original frames remaining in A start at time=0 and will not need to be moved in the file.

As the video A grows through successive append operations, the indexes in the "moov" atom will grow.  To prevent needing to relocate the potentially quite large "mdat" section, we use a "free" section which we can shrink in-place as "mdat" grows.  But if the "mdat" grows too large and exhausts the free space, A will need to be completely rewritten, with "mdat" moving.  This is very likely to happen the first time you append to A, since A probably won't have originally been created with a free section of significant size.  (And sometimes A will be created with the "moov" section after the "mdat", which reduces streaming efficiency -- see discussions around the "qtfaststart" tool).  So expect A to be rewritten on the first append.

When A is rewritten to include more free space, it's useful to know if there will be more appends in the future, and if so, much free space should be included now to allow for those future appends to not require rewriting to move "mdat".  You can specify a number of frames, in which case the additional free space will be created to allow roughly that number of frames to be appended before needing to rewrite the video.  However, when rewriting, the tool will refuse to create a free area smaller thant he current "mdat" area, meaning there should be at least enough space to double the video size.  So worst case if you chronically estimate too low, the video will be rewritten log(n) times over the long haul.

Reference:  https://developer.apple.com/library/mac/documentation/QuickTime/QTFF/QTFFChap2/qtff2.html#//apple_ref/doc/uid/TP40000939-CH204-56313

Examples:
Concatenate a.mp4 and b.mp4, writing into a.mp4, leaving 10000 frames of space for future appending

    Concatenate-mp4-videos.py a.mp4 b.mp4 --future_frames=10000

Using python-style slice syntax, you can concatenate portions of videos.

Concatenate all but last chunk of a.mp4, plus first chunk of b.mp4, plus all of c.mp4, into a.mp4:

    Concatenate-mp4-videos.py 'a.mp4[0:-1]' 'b.mp4[0:1]' c.mp4

Debug tools
-----------

    ffprobe -i video.mp4 -show_packets
