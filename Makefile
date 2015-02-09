test: test-short test-no-stss test-unequal-sizes test-trailer

test-short:
	cp short.mp4 test-short.mp4
	./Concatenate-mp4-videos.py test-short.mp4 short.mp4

test-no-stss:
	cp no-stss.mp4 test-no-stss.mp4
	./concatenate-mp4-videos.py test-no-stss.mp4 no-stss.mp4 short.mp4

test-unequal-sizes:
	cp short.mp4 test-unequal-sizes.mp4
	echo 'Expecting exception from trying to concatenate videos of different sizes'
	! ./Concatenate-mp4-videos.py test-unequal-sizes.mp4 unequal-size.mp4
	echo 'Success (exception happened, as expected)'

test-trailer:
	cp no-stss.mp4 test-no-stss-trailer.mp4
	./concatenate-mp4-videos.py test-no-stss-trailer.mp4 trailer.mp4
	cp short.mp4 test-short-trailer.mp4
	./concatenate-mp4-videos.py test-short-trailer.mp4 trailer.mp4





