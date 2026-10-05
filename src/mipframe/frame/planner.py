"""Physical operator contract, including upstream storage and spill."""
def backend(frame,fields):
    return 'typed', 'streaming scans; bounded array/mmap spill'
