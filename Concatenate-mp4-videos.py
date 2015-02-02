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

# In[1]:

import copy, json, os, pprint, re, StringIO, struct, urllib
from collections import OrderedDict

# In[2]:

class AtomWriter:
    def __init__(self, atom):
        self.out = StringIO.StringIO()
        self.write(atom['atomtype'])
        self.write(atom['version'])
        self.write(atom['flags'])
        assert len(self.out.getvalue()) == 8
    
    def write16(self, val):
        self.write(struct.pack('!H', val))
        
    def write32(self, val):
        self.write(struct.pack('!I', val))
        
    def write(self, bytes):
        self.out.write(bytes)

    def atom(self):
        data = self.out.getvalue()
        return struct.pack('!I', 4 + len(data)) + data

class AtomReader:
    def __init__(self, inp):
        self.inp = inp
        self.position = inp.tell()
        self.atomsize = self.read32()
        self.atomtype = self.inp.read(4)
        self.parsed = {
            '_position': self.position,
            'atomsize': self.atomsize,
            'atomtype': self.atomtype
        }
        self.verbose = False
        if self.verbose:
            print 'Reading %s (length %d) from position %d' % (self.atomtype, self.atomsize, self.position)
    
    def read(self, n):
        return self.inp.read(n)
    
    def read16(self):
        return struct.unpack('!H', self.read(2))[0]

    def read32(self):
        return struct.unpack('!I', self.read(4))[0]
    
    def read_version_and_flags(self):
        self.parsed['version'] = self.inp.read(1)
        self.parsed['flags'] = self.inp.read(3)
    
    # Seek to end of atom and return parse
    def skip(self):
        self.inp.seek(self.position + self.atomsize)
        return self.parsed
    
    def done(self):
        if self.inp.tell() != self.position + self.atomsize:
            raise Exception('While reading %s, atom length is %d but read %d bytes' % (self.atomtype, self.atomsize,
                                                                                       self.inp.tell() - self.position))
        return self.parsed
    
    def set(self, key, value):
        self.parsed[key] = value
        
    def get(self, key):
        return self.parsed[key]
        
class Chunk:
    def __init__(self, video, chunkno):
        self.video = video
        self.chunkno = chunkno
        chunk_offsets = video.info['moov']['trak']['mdia']['minf']['stbl']['stco']['chunk_offsets']
        self.offset = chunk_offsets[chunkno]
        # Compute first and last sample #s
        self._compute_samples()
        
    def _compute_samples(self):
        # Find first (includisve) and last (exclusive) sample #s
        sample_to_chunk_map = self.video.info['moov']['trak']['mdia']['minf']['stbl']['stsc']['sample_to_chunk_map']
        last_sample = 0
        first_sample = 0
        for i in range(0, self.chunkno + 1):
            chunk_info = sample_to_chunk_map[i]
            assert chunk_info['first_chunk'] == i + 1
            assert chunk_info['sample_description_id'] == 1
            first_sample = last_sample
            last_sample += chunk_info['samples_per_chunk']
        
        # Find sample sizes
        sample_sizes = self.video.info['moov']['trak']['mdia']['minf']['stbl']['stsz']['sample_sizes']
        self.sample_sizes = sample_sizes[first_sample : last_sample]
        self.length = sum(self.sample_sizes)
        
        # Find keyframes
        key_frame_samples = self.video.info['moov']['trak']['mdia']['minf']['stbl']['stss']['key_frame_samples']
        self.keyframes = []
        for key_frame_sample in key_frame_samples:
            key_frame_sample -= 1  # from 1-based to 0-based
            if first_sample <= key_frame_sample and key_frame_sample < last_sample:
                self.keyframes.append(key_frame_sample - first_sample)
        
    def __repr__(self):
        return ('Chunk(video=%s, index=%d, offset=%d, nsamples=%d, length=%d)' % 
                (self.video.filename, self.chunkno, self.offset, 
                 len(self.sample_sizes), self.length))

class NeedsRewriteException(Exception):
    def __init__(self, why, space_needed):
        self.value = 'Video needs rewriting because %s (space needed=%d)' % (why, space_needed)
        self.space_needed = space_needed
    def __str__(self):
        return repr(self.value)

class MP4:
    def __init__(self, filename, writable=False):
        self.writable = writable
        self.filename = filename
        self.fp = open(filename, 'r+' if writable else 'r')
        self.verbose = False
        self.info = self.parse_container()
        print 'Read %s' % filename
        
    def write32(self, val):
        return struct.pack('!I', val)
    
    def read32(self, bytes):
        return struct.unpack('!I', bytes)[0]

    def parse_mvhd(self, ar):
        ar.read_version_and_flags()
        ar.set('creation_time', ar.read32())
        ar.set('modification_time', ar.read32())
        ar.set('time_scale', ar.read32())
        ar.set('duration', ar.read32()) # scale by 1 / ar.get('time_scale'))
        ar.set('unparsed', ar.read(4 + 2 + 10 + 36 + 4*7))
        
    def unparse_mvhd(self, atom):
        aw = AtomWriter(atom)
        aw.write32(atom['creation_time'])
        aw.write32(atom['modification_time'])
        aw.write32(atom['time_scale'])
        aw.write32(atom['duration'])
        aw.write(atom['unparsed'])
        return aw.atom()

    def parse_tkhd(self, ar):
        ar.read_version_and_flags()
        ar.set('creation_time', ar.read32())
        ar.set('modification_time', ar.read32())
        ar.set('track_id', ar.read32())
        ar.set('reserved1', ar.read(4))
        ar.set('duration', ar.read32()) # scale by 1 / mvhd['time_scale']
        ar.set('unparsed', ar.read(8 + 2*4 + 36))
        ar.set('track_width', ar.read32()) # scale by 1/65536
        ar.set('track_height', ar.read32()) # scale by 1/65536

    def unparse_tkhd(self, atom):
        aw = AtomWriter(atom)
        aw.write32(atom['creation_time'])
        aw.write32(atom['modification_time'])
        aw.write32(atom['track_id'])
        assert len(atom['reserved1']) == 4
        aw.write(atom['reserved1'])
        aw.write32(atom['duration'])
        assert len(atom['unparsed']) == 8 + 2*4 + 36
        aw.write(atom['unparsed'])
        aw.write32(atom['track_width'])
        aw.write32(atom['track_height'])
        return aw.atom()
    
    def parse_elst(self, ar):
        ar.read_version_and_flags()
        count = ar.read32()
        edits = []
        for i in range(0, count):
            elt = {}
            elt['duration'] = ar.read32() # scale by 1 / mvhd['time_scale']
            elt['start_time'] = ar.read32()
            elt['rate'] = ar.read32() # scale by 1/65536
            edits.append(elt)
        ar.set('edits', edits)

    def unparse_elst(self, atom):
        aw = AtomWriter(atom)
        aw.write32(len(atom['edits']))
        for edit in atom['edits']:
            aw.write32(edit['duration'])
            aw.write32(edit['start_time'])
            aw.write32(edit['rate'])
        return aw.atom()

    def parse_mdhd(self, ar):
        ar.read_version_and_flags()
        ar.set('creation_time', ar.read32())
        ar.set('modification_time', ar.read32())
        ar.set('time_scale', ar.read32())
        ar.set('duration', ar.read32()) # scale by 1/time_scale
        ar.set('language', ar.read16())
        ar.set('quality', ar.read16())

    def unparse_mdhd(self, atom):
        aw = AtomWriter(atom)
        aw.write32(atom['creation_time'])
        aw.write32(atom['modification_time'])
        aw.write32(atom['time_scale'])
        aw.write32(atom['duration'])
        aw.write16(atom['language'])
        aw.write16(atom['quality'])
        return aw.atom()

    # Chunk offset table
    def parse_stco(self, ar):
        ar.read_version_and_flags()
        num = ar.read32()
        ret = []
        for i in range(0, num):
            ret.append(ar.read32())
        ar.set('chunk_offsets', ret)

    def unparse_stco(self, atom):
        aw = AtomWriter(atom)
        chunk_offsets = atom['chunk_offsets']
        aw.write32(len(chunk_offsets))
        for offset in chunk_offsets:
            aw.write32(offset)
        return aw.atom()
    
    # Sample size table.  For every frame, how large is it in bytes?
    def parse_stsz(self, ar):
        ar.read_version_and_flags()
        sample_size = ar.read32()
        if sample_size != 0:
            raise Exception('sample_size of != 0 is unimplemented' % sample_size)
        num = ar.read32()
        sample_sizes = []
        for i in range(0, num):
            sample_sizes.append(ar.read32())
        ar.set('sample_sizes', sample_sizes)
        
    def unparse_stsz(self, atom):
        aw = AtomWriter(atom)
        aw.write32(0) # fixed sample_size = 0 means samples are of variable size
        aw.write32(len(atom['sample_sizes']))
        for sample_size in atom['sample_sizes']:
            aw.write32(sample_size)
        return aw.atom()
        
    # Sample to chunk map
    def parse_stsc(self, ar):
        ar.read_version_and_flags()
        num = ar.read32()
        ret = []
        for i in range(0, num):
            entry = {}
            entry['first_chunk'] = ar.read32()
            entry['samples_per_chunk'] = ar.read32()
            entry['sample_description_id'] = ar.read32()
            ret.append(entry)
        ar.set('sample_to_chunk_map', ret)

    def unparse_stsc(self, atom):
        aw = AtomWriter(atom)
        aw.write32(len(atom['sample_to_chunk_map']))
        for entry in atom['sample_to_chunk_map']:
            aw.write32(entry['first_chunk'])
            aw.write32(entry['samples_per_chunk'])
            aw.write32(entry['sample_description_id'])
        return aw.atom()

    # stss: sync to sample (keyframes)
    def parse_stss(self, ar):
        ar.read_version_and_flags()
        num = ar.read32()
        key_frame_samples = []
        for i in range(0, num):
            key_frame_samples.append(ar.read32())
        ar.set('key_frame_samples', key_frame_samples)

    # stss: sync to sample (keyframes)
    def unparse_stss(self, atom):
        aw = AtomWriter(atom)
        aw.write32(len(atom['key_frame_samples']))
        for key_frame_sample in atom['key_frame_samples']:
            aw.write32(key_frame_sample)
        return aw.atom()

    # Time to sample
    def parse_stts(self, ar):
        ar.read_version_and_flags()
        num = ar.read32()
        ret = []
        for i in range(0, num):
            entry = {}
            entry['sample_count'] = ar.read32()
            entry['sample_duration'] = ar.read32() # scale using mdhd.time_scale
            ret.append(entry)
        ar.set('time_to_sample_map', ret)
    
    # Time to sample
    def unparse_stts(self, atom):
        aw = AtomWriter(atom)
        aw.write32(len(atom['time_to_sample_map']))
        for entry in atom['time_to_sample_map']:
            aw.write32(entry['sample_count'])
            aw.write32(entry['sample_duration'])
        return aw.atom()
    
    def parse_container(self, offset0=0, offset1=None, prefix=''):
        "Walk the atom tree in a mp4 file"
        if offset1 == None:
            self.fp.seek(0, 2)
            offset1 = self.fp.tell()
            self.fp.seek(offset0)
        offset= offset0
        ret = OrderedDict()
        while offset < offset1:
            if self.fp.tell() != offset:
                raise Exception('File offset is %d, but expected %d, in parse_container' % (self.fp.tell(), offset))
            ar = AtomReader(self.fp)
            
            parser_name = 'parse_' + ar.atomtype
            if parser_name in dir(self):
                if self.verbose:
                    print 'Found %s size %d' % (prefix + atomtype, atomsize)
                getattr(self, parser_name)(ar)
                val = ar.done()
                test_unparse = False
                if test_unparse:
                    self.fp.seek(offset)
                    bytes = self.fp.read(ar.atomsize)
                    if bytes == getattr(self, 'unparse_' + ar.atomtype)(val):
                        print 'parse and unparse match'
                    else:
                        raise Exception('parse and unparse do not match')
            elif ar.atomtype in ['meta', 'moov', 'trak', 'mdia', 'minf', 'edts', 'dinf',
                                 'stbl', 'udta']:
                container_header = ''
                if ar.atomtype == 'meta':
                    container_header = ar.read(4)
                val = self.parse_container(offset + 8 + len(container_header), offset + ar.atomsize, prefix + ar.atomtype + '.')
                val['_container_header'] = container_header
                for (k, v) in ar.done().iteritems():
                    val[k] = v
            elif ar.atomtype in ['ftyp', 'mdhd', 'hdlr', 'mdat',
                                 'vmhd', 'dref', 'stsd', 'stts', 'stss', 'stsc',
                                 'stsz', 'stco', 'ilst', 'free']:
                val = ar.skip()
            else:
                raise Exception('Unknown atom type "%s"' % ar.atomtype)
            ret[ar.atomtype] = val
            offset += ar.atomsize
        return ret

    def write_atom(self, atom):
        unparser_name = 'unparse_' + atom['atomtype']
        if unparser_name in dir(self):
            ret = getattr(self, unparser_name)(atom)
        elif '_container_header' in atom:
            ret = atom['atomtype']
            ret += atom['_container_header']
            for (child_type, child_atom) in atom.iteritems():
                if len(child_type) == 4:
                    ret += self.write_atom(child_atom)
            ret = self.write32(len(ret) + 4) + ret
        else:
            self.fp.seek(atom['_position'])
            ret = self.fp.read(atom['atomsize'])
        assert self.read32(ret[0:4]) == len(ret)
        return ret
    
    def write_free_atom(self, space):
        return self.write32(space + 8) + 'free' + ('\x00' * space)

    def find_atom(self, atomtype):
        return self._find_atom(self.info, atomtype)
    
    def _find_atom(self, container, atomtype):
        for (key, val) in container.iteritems():
            if key == atomtype:
                return atomtype
            if len(key) == 4:
                ret = self._find_atom(val, atomtype)
                if ret:
                    return key + '.' + ret
        return None
    
    def copy_with_padding(self, dest, padding):
        moov = copy.deepcopy(self.info['moov'])
        new_mdat_location = (self.info['ftyp']['atomsize'] +
                             self.info['moov']['atomsize'] +
                             8 +
                             padding)
        mdat_move = new_mdat_location - self.info['mdat']['_position']
        print 'Moving mdat by %d bytes' % mdat_move
        stco = moov['trak']['mdia']['minf']['stbl']['stco']
        chunk_offsets = stco['chunk_offsets']
        for i in range(0, len(chunk_offsets)):
            chunk_offsets[i] += mdat_move
                
        with open(dest, 'w') as out:
            out.write(self.write_atom(self.info['ftyp']))
            out.write(self.write_atom(moov))
            out.write(self.write_free_atom(padding))
            out.write(self.write_atom(self.info['mdat']))
            
        print 'Wrote %s' % dest
    
    def chunks(self):
        chunk_offsets = self.info['moov']['trak']['mdia']['minf']['stbl']['stco']['chunk_offsets']
        chunks = [Chunk(self, i) for i in range(0, len(chunk_offsets))]
        
        # Check that chunks are contiguous in the mdat
        pos = self.info['mdat']['_position'] + 8
        for (i, chunk) in enumerate(chunks):
            if chunk.offset != pos:
                raise Exception('Expected chunk %d to start at %d, but stco says %d' % (i, pos, chunk.offset))
            pos += chunk.length
        assert pos == self.info['mdat']['_position'] + self.info['mdat']['atomsize']
        return chunks
    
    def update_in_place_using_chunks(self, chunks):
        """Modify video to consist of chunks.  To append video B to the end of video A:
           a.append(a.chunks() + b.chunks())"""
        
        if not self.writable:
            raise Exception('Please instantiate MP4 with writable=True in order to call update_in_place_with_chunks')
        
        # Make sure video has moov, free, mdat in that order
        if not 'free' in self.info:
            raise NeedsRewriteException('missing free section')
        
        if (self.info['moov']['_position'] > self.info['free']['_position'] or
            self.info['free']['_position'] > self.info['mdat']['_position']):
            raise NeedsRewriteException('sections disordered')
        
        moov = copy.deepcopy(self.info['moov'])

        # Compute duration
        sample_duration = moov['trak']['mdia']['minf']['stbl']['stts']['time_to_sample_map'][0]['sample_duration']
        mdhd_time_scale = moov['trak']['mdia']['mdhd']['time_scale']
        nsamples = 0
        for chunk in chunks:
            nsamples += len(chunk.sample_sizes)

        fps = float(mdhd_time_scale) / sample_duration
        duration = float(nsamples * sample_duration) / mdhd_time_scale

        print 'New duration %.3f sec (%d samples at %.5f FPS)' % (duration, nsamples, fps)

        # Adjust mdhd (media header) duration
        moov['trak']['mdia']['mdhd']['duration'] = sample_duration * nsamples

        # Adjust mvhd (movie header) duration
        moov['mvhd']['duration'] = int(duration * moov['mvhd']['time_scale'] + 0.5)

        # Adjust tkhd (track header) duration
        moov['trak']['tkhd']['duration'] = int(duration * moov['mvhd']['time_scale'] + 0.5)

        # Adjust elst (edit list) duration
        edits = moov['trak']['edts']['elst']['edits']
        assert len(edits) == 1 # If more than one edit, we need new code to handle
        assert edits[0]['rate'] == 65536 # 1.0 in fixed point
        edits[0]['duration'] = int(duration * moov['mvhd']['time_scale'] + 0.5)

        # Construct new list of offsets for stco (chunk offsets)
        stco = moov['trak']['mdia']['minf']['stbl']['stco']
        stco['chunk_offsets'] = []
        pos = self.info['mdat']['_position'] + 8  # Position of first chunk
        for chunk in chunks:
            stco['chunk_offsets'].append(pos)
            pos += chunk.length

        # Construct new list of all sample (frame) sizes for stsz (sample sizes)
        stsz = moov['trak']['mdia']['minf']['stbl']['stsz']
        stsz['sample_sizes'] = []
        for chunk in chunks:
           stsz['sample_sizes'].extend(chunk.sample_sizes)

        # Construct new list of entries for stsc (chunk to sample map)
        stsc = moov['trak']['mdia']['minf']['stbl']['stsc']
        stsc['sample_to_chunk_map'] = []
        for (i, chunk) in enumerate(chunks):
            stsc['sample_to_chunk_map'].append({
                'first_chunk': i + 1,
                'samples_per_chunk': len(chunk.sample_sizes),
                'sample_description_id': 1
            })

        # Construct new list of keyframes for stss (keyframe list)
        stss = moov['trak']['mdia']['minf']['stbl']['stss']
        stss['key_frame_samples'] = []
        sampleno = 1 # 1-based
        for chunk in chunks:
            for keyframe in chunk.keyframes:
                stss['key_frame_samples'].append(sampleno + keyframe)
            sampleno += len(chunk.sample_sizes)

        # Adjust sample count for stts (time to sample map)
        stts = moov['trak']['mdia']['minf']['stbl']['stts']
        assert len(stts['time_to_sample_map']) == 1
        stts['time_to_sample_map'][0]['sample_count'] = nsamples
        
        # Create new moov section
        moov_out = self.write_atom(moov)
        free_len = self.info['mdat']['_position'] - moov['_position'] - len(moov_out) - 8
        if free_len < 0:
            raise NeedsRewriteException('not enough free space', -free_len)

        # Modify mdat in place with new chunks
        self.fp.seek(self.info['mdat']['_position'])

        # Compute new length of mdat in bytes
        length = 8
        for chunk in chunks:
            length += chunk.length

        begin_mdat = self.fp.tell()
        self.fp.write(self.write32(length))
        self.fp.write('mdat')

        for chunk in chunks:
            # Copying from self?  Make sure we haven't tried to move the chunk
            if chunk.video == self:
                assert chunk.offset == self.fp.tell()
                # Skip
                self.fp.seek(chunk.length + self.fp.tell())
            else:
                chunk.video.fp.seek(chunk.offset)
                self.fp.write(chunk.video.fp.read(chunk.length))

        assert(self.fp.tell() == begin_mdat + length)

        self.fp.truncate()

        # Rewrite moov and free

        self.fp.seek(moov['_position'])
        print 'Writing moov (%d bytes) at position %d' % (len(moov_out), self.fp.tell())
        self.fp.write(moov_out)
        
        print 'Writing free (%d bytes) at position %d (end=%d)' % (free_len + 8, self.fp.tell(), 
                                                                   self.fp.tell() + free_len + 8)
        self.fp.write(self.write_free_atom(free_len))
        assert self.fp.tell() == self.info['mdat']['_position']

        print 'Updated %s, length %d' % (self.filename, os.stat(self.filename).st_size)    


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

def append(filenames_and_chunks, future_frames=1000):
    while True:
        chunks = parse_filename_and_chunks(filenames_and_chunks[0], writable=True)

        for file in filenames_and_chunks[1:]:
            chunks.extend(parse_filename_and_chunks(file))

        dest = chunks[0].video

        try:
            dest.update_in_place_using_chunks(chunks)
        except NeedsRewriteException as e:
            print e
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
    
    args = parser.parse_args()
    append(args.filenames_and_chunks, args.future_frames)

if __name__ == "__main__":
    main()

