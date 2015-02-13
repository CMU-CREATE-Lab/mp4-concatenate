test: \
	test-short \
	test-future-frames \
	test-no-stss \
	test-unequal-sizes \
	test-trailer-short \
	test-short-trailer \
        test-more-chunks-than-stsc

test-short:
	cp short.mp4 test-short.mp4
	./Concatenate-mp4-videos.py test-short.mp4 short.mp4

test-future-frames:
	cp short.mp4 test-short.mp4
	./Concatenate-mp4-videos.py --future_frames 10000 test-short.mp4 short.mp4

test-no-stss:
	cp no-stss.mp4 test-no-stss.mp4
	./Concatenate-mp4-videos.py test-no-stss.mp4 no-stss.mp4 short.mp4

test-unequal-sizes:
	cp short.mp4 test-unequal-sizes.mp4
	echo 'Expecting exception from trying to concatenate videos of different sizes'
	! ./Concatenate-mp4-videos.py test-unequal-sizes.mp4 unequal-size.mp4
	echo 'Success (exception happened, as expected)'

test-trailer-short:
	cp trailer.mp4 test-trailer-short.mp4
	./Concatenate-mp4-videos.py test-trailer-short.mp4 short.mp4

test-short-trailer:
	cp short.mp4 test-short-trailer.mp4
	./Concatenate-mp4-videos.py test-short-trailer.mp4 trailer.mp4

test-more-chunks-than-stsc:
	cp more-chunks-than-stsc.mp4 test-more-chunks-than-stsc.mp4
	./Concatenate-mp4-videos.py test-more-chunks-than-stsc.mp4 more-chunks-than-stsc.mp4
