""" Perturb regions in bedfiles """

import logging
import os
import sys
import random
import logmuse
import pandas as pd
import numpy as np
import pyranges as pr

from bedshift._version import __version__
from bedshift import arguments
from bedshift import BedshiftYAMLHandler

_LOGGER = logging.getLogger(__name__)

__all__ = ["Bedshift"]


class Bedshift(object):
    """
    The bedshift object with methods to perturb regions
    """

    def __init__(self, bedfile_path, chrom_sizes=None, delimiter='\t'):
        """
        Read in a .bed file to pandas DataFrame format

        :param str bedfile_path: the path to the BED file
        :param str chrom_sizes: the path to the chrom.sizes file
        :param str delimiter: the delimiter used in the BED file
        """
        self.bedfile_path = bedfile_path
        self.chrom_lens = {}
        if chrom_sizes:
            self._read_chromsizes(chrom_sizes)
        df = self.read_bed(bedfile_path, delimiter=delimiter)
        self.original_num_regions = df.shape[0]
        self.bed = df.astype({0: 'object', 1: 'int64', 2: 'int64', 3: 'object'}) \
                            .sort_values([0, 1, 2]).reset_index(drop=True)
        self.original_bed = self.bed.copy()


    def _read_chromsizes(self, fp):
        try:
            with open(fp) as f:
                for line in f:
                    line = line.strip().split('\t')
                    chrom = str(line[0])
                    size = int(line[1])
                    self.chrom_lens[chrom] = size
        except FileNotFoundError:
            _LOGGER.error("fasta file path {} invalid".format(fp))
            sys.exit(1)

        total_len = sum(self.chrom_lens.values())
        self.chrom_weights = [chrom_len / total_len for chrom_len in self.chrom_lens.values()]


    def reset_bed(self):
        """
        Reset the stored bedfile to the state before perturbations
        """
        self.bed = self.original_bed.copy()


    def _precheck(self, rate, requiresChromLens=False, isAdd=False):
        if isAdd:
            if rate < 0:
                _LOGGER.error("Rate must be between 0 and 1")
                sys.exit(1)
        else:
            if rate < 0 or rate > 1:
                _LOGGER.error("Rate must be between 0 and 1")
                sys.exit(1)
        if requiresChromLens:
            if len(self.chrom_lens) == 0:
                _LOGGER.error("chrom.sizes file must be specified when shifting regions")
                sys.exit(1)


    def pick_random_chroms(self, n):
        """
        Utility function to pick a random chromosome

        :return str, float chrom_str, chrom_len: chromosome number and length
        """
        chrom_strs = random.choices(list(self.chrom_lens.keys()), weights=self.chrom_weights, k=n)
        chrom_lens = [self.chrom_lens[chrom_str] for chrom_str in chrom_strs]
        return zip(chrom_strs, chrom_lens)


    def add(self, addrate, addmean, addstdev):
        """
        Add regions

        :param float addrate: the rate to add regions
        :param float addmean: the mean length of added regions
        :param float addstdev: the standard deviation of the length of added regions
        :return int: the number of regions added
        """
        self._precheck(addrate, requiresChromLens=True, isAdd=True)

        rows = self.bed.shape[0]
        num_add = int(rows * addrate)
        new_regions = {0: [], 1: [], 2: [], 3: []}
        random_chroms = self.pick_random_chroms(num_add)
        for chrom_str, chrom_len in random_chroms:
            start = random.randint(1, chrom_len)
            # ensure chromosome length is not exceeded
            end = min(start + int(np.random.normal(addmean, addstdev)), chrom_len)
            new_regions[0].append(chrom_str)
            new_regions[1].append(start)
            new_regions[2].append(end)
            new_regions[3].append('A')
        self.bed = self.bed.append(pd.DataFrame(new_regions), ignore_index=True)
        return num_add


    def add_from_file(self, fp, addrate, delimiter='\t'):
        """
        Add regions from another bedfile to this perturbed bedfile

        :param float addrate: the rate to add regions
        :param str fp: the filepath to the other bedfile
        :return int: the number of regions added
        """
        self._precheck(addrate, requiresChromLens=True, isAdd=True)

        rows = self.bed.shape[0]
        num_add = int(rows * addrate)
        df = self.read_bed(fp, delimiter=delimiter)
        if num_add > df.shape[0]:
            num_add = df.shape[0]
        add_rows = random.sample(list(range(df.shape[0])), num_add)
        add_df = df.loc[add_rows].reset_index(drop=True)
        add_df[3] = pd.Series(['A'] * add_df.shape[0])
        self.bed = self.bed.append(add_df, ignore_index=True)
        return num_add


    def shift(self, shiftrate, shiftmean, shiftstdev, shift_rows=None):
        """
        Shift regions

        :param float shiftrate: the rate to shift regions (both the start and end are shifted by the same amount)
        :param float shiftmean: the mean shift distance
        :param float shiftstdev: the standard deviation of the shift distance
        :return int: the number of regions shifted
        """
        self._precheck(shiftrate, requiresChromLens=True)

        rows = self.bed.shape[0]
        if shift_rows == None:
            shift_rows = random.sample(list(range(rows)), int(rows * shiftrate))
        new_row_list = []
        to_drop = []
        num_shifted = 0
        invalid_shifted = 0
        for row in shift_rows:
            drop_row, new_region = self._shift(row, shiftmean, shiftstdev) # shifted rows display a 1
            if drop_row is not None and new_region is not None:
                num_shifted += 1
                new_row_list.append(new_region)
                to_drop.append(drop_row)
            else:
                invalid_shifted += 1
        self.bed = self.bed.drop(to_drop)
        self.bed = self.bed.append(new_row_list, ignore_index=True)
        self.bed = self.bed.reset_index(drop=True)
        if invalid_shifted > 0:
            _LOGGER.warning(f"{invalid_shifted} regions were prevented from being shifted outside of chromosome boundaries. Reported regions shifted will be less than expected.")
        return num_shifted


    def _shift(self, row, mean, stdev):
        theshift = int(np.random.normal(mean, stdev))

        chrom = self.bed.loc[row][0]
        start = self.bed.loc[row][1]
        end = self.bed.loc[row][2]
        _LOGGER.debug("Chrom lengths: {}".format(str(self.chrom_lens)))
        _LOGGER.debug("chrom: {}".format(str(chrom)))
        if start + theshift < 0 or end + theshift > self.chrom_lens[str(chrom)]:
            # check if the region is shifted out of chromosome length bounds
            return None, None

        return row, {0: chrom, 1: start + theshift, 2: end + theshift, 3: 'S'}


    def shift_from_file(self, fp, shiftrate, shiftmean, shiftstdev, delimiter='\t'):
        self._precheck(shiftrate)

        rows = self.bed.shape[0]
        num_shift = int(rows * shiftrate)
        shift_bed = self.read_bed(fp, delimiter=delimiter)

        intersect_regions = self._find_overlap(fp)
        try:
            rows2shift = random.sample(list(range(len(intersect_regions))), num_shift)
            return self.shift(shiftrate, shiftmean, shiftstdev, rows2shift)
        except ValueError:
            bedname = os.path.basename(self.bedfile_path)
            shift_file_name = os.path.basename(fp)
            _LOGGER.error("The number of overlapping regions between "+
                "{} and {} is {} but the shift ratio provided is trying to shift {} regions."\
                .format(bedname, shift_file_name, len(intersect_regions), num_shift))
            sys.exit(1)

    def cut(self, cutrate):
        """
        Cut regions to create two new regions

        :param float cutrate: the rate to cut regions into two separate regions
        :return int: the number of regions cut
        """
        self._precheck(cutrate)

        rows = self.bed.shape[0]
        cut_rows = random.sample(list(range(rows)), int(rows * cutrate))
        new_row_list = []
        to_drop = []
        for row in cut_rows:
            drop_row, new_regions = self._cut(row) # cut rows display a 2
            new_row_list.extend(new_regions)
            to_drop.append(drop_row)
        self.bed = self.bed.drop(to_drop)
        self.bed = self.bed.append(new_row_list, ignore_index=True)
        self.bed = self.bed.reset_index(drop=True)
        return len(cut_rows)


    def _cut(self, row):
        chrom = self.bed.loc[row][0]
        start = self.bed.loc[row][1]
        end = self.bed.loc[row][2]

        # choose where to cut the region
        thecut = (start + end) // 2 # int(np.random.normal((start+end)/2, (end - start)/6))
        if thecut <= start:
            thecut = start + 10
        if thecut >= end:
            thecut = end - 10

        ''' may add in later, this makes the api confusing!
        # adjust the cut regions using the shift function
        new_segs = self.__shift(new_segs, 0, meanshift, stdevshift)
        new_segs = self.__shift(new_segs, 1, meanshift, stdevshift)
        '''

        return row, [{0: chrom, 1: start, 2: thecut, 3: 'C'}, {0: chrom, 1: thecut, 2: end, 3: 'C'}]

    def merge(self, mergerate):
        """
        Merge two regions into one new region

        :param float mergerate: the rate to merge two regions into one
        :return int: number of regions merged
        """
        self._precheck(mergerate)

        rows = self.bed.shape[0]
        merge_rows = random.sample(list(range(rows)), int(rows * mergerate))
        to_add = []
        to_drop = []
        for row in merge_rows:
            drop_row, add_row = self._merge(row)
            if add_row:
                to_add.append(add_row)
            to_drop.extend(drop_row)
        self.bed = self.bed.drop(to_drop)
        self.bed = self.bed.append(to_add, ignore_index=True)
        self.bed = self.bed.reset_index(drop=True)
        return len(merge_rows)


    def _merge(self, row):
        # check if the regions being merged are on the same chromosome
        if row + 1 not in self.bed.index or self.bed.loc[row][0] != self.bed.loc[row+1][0]:
            return [], None

        chrom = self.bed.loc[row][0]
        start = self.bed.loc[row][1]
        end = self.bed.loc[row+1][2]
        return [row, row+1], {0: chrom, 1: start, 2: end, 3: 'M'}

    def drop(self, droprate):
        """
        Drop regions

        :param float droprate: the rate to drop/remove regions
        :return int: the number of rows dropped
        """
        self._precheck(droprate)

        rows = self.bed.shape[0]
        drop_rows = random.sample(list(range(rows)), int(rows * droprate))
        self.bed = self.bed.drop(drop_rows)
        self.bed = self.bed.reset_index(drop=True)
        return len(drop_rows)


    def _find_overlap(self, fp, reference=None):
        """
        find intersecting regions between the reference bedfile and the comparison file provided in the yaml config file.
        """
        if reference is None:
            reference_bed = self.original_bed.copy()
        else:
            if isinstance(reference, pd.DataFrame):
                reference_bed = reference.copy()
            elif isinstance(reference, str):
                reference_bed = self.read_bed(reference)
            else:
                raise Exception("unsupported input type: {}".format(type(reference)))
        if isinstance(fp, pd.DataFrame):
            comparison_bed = fp.copy()
        elif isinstance(fp, str):
            comparison_bed = self.read_bed(fp)
        else:
            raise Exception("unsupported input type: {}".format(type(reference)))
        reference_bed.columns = ['Chromosome', 'Start', 'End', 'modifications']
        comparison_bed.columns = ['Chromosome', 'Start', 'End', 'modifications']
        reference_pr = pr.PyRanges(reference_bed)
        comparison_pr = pr.PyRanges(comparison_bed)
        intersection = reference_pr.overlap(comparison_pr, how='first').as_df()
        if len(intersection) == 0:
            raise Exception("no intersection found between {} and {}".format(reference_bed, comparison_bed))
        intersection = intersection.drop(['modifications'], axis=1)
        intersection.columns = [0, 1, 2]
        return intersection


    def drop_from_file(self, fp, droprate, delimiter='\t'):
        """
        drop regions that overlap between the reference bedfile and the provided bedfile.

        :param float droprate: the rate to drop regions
        :param str fp: the filepath to the other bedfile containing regions to be dropped
        :return int: the number of regions dropped
        """
        if droprate < 0:
            _LOGGER.error("Rate must be greater than or equal to 0")
            sys.exit(1)
        if droprate == 0:
            return 0

        rows = self.bed.shape[0]
        num_drop = int(rows * droprate)
        drop_bed = self.read_bed(fp, delimiter=delimiter)
        drop_rows = drop_bed.shape[0]

        if num_drop >= drop_rows:
            _LOGGER.warning("Number of regions to be dropped ({}) is larger than the provided bedfile size ({}). Dropping {} regions.".format(num_drop, drop_rows, drop_rows))
            num_drop = drop_rows
        intersect_regions = self._find_overlap(fp)
        rows2drop = random.sample(list(range(len(intersect_regions))), num_drop)

        self.bed = self.bed.drop(intersect_regions.index[rows2drop]).reset_index(drop=True)
        return num_drop


    def all_perturbations(self,
                          addrate=0.0, addmean=320.0, addstdev=30.0,
                          addfile=None,
                          shiftrate=0.0, shiftmean=0.0, shiftstdev=150.0,
                          shiftfile=None,
                          cutrate=0.0,
                          mergerate=0.0,
                          droprate=0.0,
                          dropfile=None,
                          yaml=None,
                          bedshifter=None):
        '''
        Perform all five perturbations in the order of shift, add, cut, merge, drop.

        :param float addrate: the rate (as a proportion of the total number of regions) to add regions
        :param float addmean: the mean length of added regions
        :param float addstdev: the standard deviation of the length of added regions
        :param float addfile: the file containing regions to be added
        :param float shiftrate: the rate to shift regions (both the start and end are shifted by the same amount)
        :param float shiftmean: the mean shift distance
        :param float shiftstdev: the standard deviation of the shift distance
        :param float shiftfile: the file containing regions to be shifted
        :param float cutrate: the rate to cut regions into two separate regions
        :param float mergerate: the rate to merge two regions into one
        :param float droprate: the rate to drop/remove regions
        :param float dropfile: the file containing regions to be dropped
        :param string yaml: the yaml_config filepath
        :param string bedshifter: Bedshift instance
        :return int: the number of total regions perturbed
        '''
        if yaml:
            return BedshiftYAMLHandler.BedshiftYAMLHandler(bedshifter, yaml).handle_yaml()
        n = 0
        if shiftfile:
            n += self.shift_from_file(shiftfile, shiftrate, shiftmean, shiftstdev)
        else:
            n += self.shift(shiftrate, shiftmean, shiftstdev)
        if addfile:
            n += self.add_from_file(addfile, addrate)
        else:
            n += self.add(addrate, addmean, addstdev)
        n += self.cut(cutrate)
        n += self.merge(mergerate)
        if dropfile:
            n += self.drop_from_file(dropfile, droprate)
        else:
            n += self.drop(droprate)
        return n


    def to_bed(self, outfile_name):
        """
        Write a pandas dataframe back into BED file format

        :param str outfile_name: The name of the output BED file
        """
        self.bed.sort_values([0,1,2], inplace=True)
        self.bed.to_csv(outfile_name, sep='\t', header=False, index=False, float_format='%.0f')
        _LOGGER.info('The output bedfile located in {} has {} regions. The original bedfile had {} regions.' \
              .format(outfile_name, self.bed.shape[0], self.original_num_regions))


    def read_bed(self, bedfile_path, delimiter='\t'):
        """
        Read a BED file into pandas dataframe

        :param str bedfile_path: The path to the BED file
        """
        try:
            df = pd.read_csv(bedfile_path, sep=delimiter, header=None, usecols=[0,1,2], engine='python')
        except FileNotFoundError:
            _LOGGER.error("BED file path {} invalid".format(bedfile_path))
            sys.exit(1)
        except:
            _LOGGER.error("file {} could not be read".format(bedfile_path))
            sys.exit(1)

        # if there is a header line in the table, remove it
        if not str(df.iloc[0, 1]).isdigit():
            df = df[1:]

        df[3] = '-' # column indicating which modifications were made
        return df


def main():
    """ Primary workflow """

    parser = logmuse.add_logging_options(arguments.build_argparser())
    args, remaining_args = parser.parse_known_args()
    global _LOGGER
    _LOGGER = logmuse.logger_via_cli(args)

    _LOGGER.info("Welcome to bedshift version {}".format(__version__))
    _LOGGER.info("Shifting file: '{}'".format(args.bedfile))

    if not args.bedfile:
        parser.print_help()
        _LOGGER.error("No BED file given")
        sys.exit(1)

    if args.chrom_lengths:
        pass
    elif args.genome:
        try:
            import refgenconf
            rgc = refgenconf.RefGenConf(refgenconf.select_genome_config())
            args.chrom_lengths = rgc.seek(args.genome, "fasta", None, "chrom_sizes")
        except ModuleNotFoundError:
            _LOGGER.error("You must have package refgenconf installed to use a refgenie genome")
            sys.exit(1)
    else:
        if args.addrate > 0 or args.shiftrate > 0:
            _LOGGER.error("You must provide either chrom sizes or a refgenie genome.")
            sys.exit(1)

    msg = arguments.param_msg

    if args.repeat < 1:
        _LOGGER.error("repeats specified is less than 1")
        sys.exit(1)

    if args.outputfile:
        outfile = args.outputfile
    else:
        outfile = 'bedshifted_{}'.format(os.path.basename(args.bedfile))

    _LOGGER.info(msg.format(
        bedfile=args.bedfile,
        chromsizes=args.chrom_lengths,
        droprate=args.droprate,
        dropfile=args.dropfile,
        addrate=args.addrate,
        addmean=args.addmean,
        addstdev=args.addstdev,
        addfile=args.addfile,
        shiftrate=args.shiftrate,
        shiftmean=args.shiftmean,
        shiftstdev=args.shiftstdev,
        shiftfile=args.shiftfile,
        cutrate=args.cutrate,
        mergerate=args.mergerate,
        outputfile=outfile,
        repeat=args.repeat,
        yaml_config=args.yaml_config))


    bedshifter = Bedshift(args.bedfile, args.chrom_lengths)
    for i in range(args.repeat):
        n = bedshifter.all_perturbations(args.addrate, args.addmean, args.addstdev,
                                         args.addfile,
                                         args.shiftrate, args.shiftmean, args.shiftstdev,
                                         args.shiftfile,
                                         args.cutrate,
                                         args.mergerate,
                                         args.droprate,
                                         args.dropfile,
                                         args.yaml_config,
                                         bedshifter)
        _LOGGER.info("\t" + str(n) + " regions changed in total.\n")
        if args.repeat == 1:
            bedshifter.to_bed(outfile)
        else:
            modified_outfile = outfile.rsplit("/")
            modified_outfile[-1] = "rep" + str(i+1) + "_" + modified_outfile[-1]
            modified_outfile = "/".join(modified_outfile)
            bedshifter.to_bed(modified_outfile)
        bedshifter.reset_bed()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _LOGGER.error("Program canceled by user!")
        sys.exit(1)
