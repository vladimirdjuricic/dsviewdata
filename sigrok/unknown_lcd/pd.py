##
# This file is part of the libsigrokdecode project.
##
# Copyright (C) 2013-2016 Uwe Hermann <uwe@hermann-uwe.de>
##
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
##
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
##
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.
##
import sys
import sdl2
import sdl2.ext
import sigrokdecode as srd
from common.srdhelper import bitpack

'''
OUTPUT_PYTHON format:

Packet:
[<ptype>, <pdata>]

<ptype>, <pdata>
 - 'ITEM', [<item>, <itembitsize>]
 - 'WORD', [<word>, <wordbitsize>, <worditemcount>]

<item>:
 - A single item (a number). It can be of arbitrary size. The max. number
   of bits in this item is specified in <itembitsize>.

<itembitsize>:
 - The size of an item (in bits). For a 4-bit parallel bus this is 4,
   for a 16-bit parallel bus this is 16, and so on.

<word>:
 - A single word (a number). It can be of arbitrary size. The max. number
   of bits in this word is specified in <wordbitsize>. The (exact) number
   of items in this word is specified in <worditemcount>.

<wordbitsize>:
 - The size of a word (in bits). For a 2-item word with 8-bit items
   <wordbitsize> is 16, for a 3-item word with 4-bit items <wordbitsize>
   is 12, and so on.

<worditemcount>:
 - The size of a word (in number of items). For a 4-item word (no matter
   how many bits each item consists of) <worditemcount> is 4, for a 7-item
   word <worditemcount> is 7, and so on.
'''


def channel_list(num_channels):
    # l = [{'id': 'cs1', 'name': 'CS1', 'desc': 'Cable select 1'},
    #      {'id': 'cs2', 'name': 'CS2', 'desc': 'Cable select 2'},
    #      {'id': 'rst', 'name': 'RST', 'desc': 'Reset - Active high'},
    #      {'id': 'rw', 'name': 'R/W', 'desc': 'Read/Write'},
    #      {'id': 'di', 'name': 'DI/RS', 'desc': 'Data Instruction'},
    #      {'id': 'en', 'name': 'Enable', 'desc': 'Clock operation enable'}
    #      ]
    # l = []

    l = [{'id': 'cs1', 'name': 'CS1', 'desc': 'Chip Select 1'},
         {'id': 'cs2', 'name': 'CS2', 'desc': 'Chip Select 2'},
         {'id': 'clk', 'name': 'Clock', 'desc': 'Clock'},
         {'id': 'rw', 'name': 'RW', 'desc': 'RW'},
         {'id': 'e', 'name': 'Enable', 'desc': 'Enable device'}]
    # for i in range(num_channels):
    #     d = {'id': 'd%d' % i, 'name': 'D%d' % i, 'desc': 'Data line %d' % i}

    for i in range(num_channels):
        d = {'id': 'd%d' % i, 'name': 'D%d' % i, 'desc': 'Data line %d' % i}
        l.append(d)

    return tuple(l)


class ChannelError(Exception):
    pass


NUM_CHANNELS = 8


class Decoder(srd.Decoder):
    api_version = 3
    id = 'ks010x'
    name = 'KS0107/8'
    longname = 'Parallel LCD driver decoder KS0107/KS0108'
    desc = 'Generic parallel synchronous bus.'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = ['ks010x']
    tags = ['Util']
    optional_channels = channel_list(NUM_CHANNELS)
    options = (
        {'id': 'sample_rate', 'desc': 'Sample Rate in Hz',
            'default': '20000000', 'values': ('1000000', '10000000', '20000000', '40000000')},
        {'id': 'clock_edge', 'desc': 'Clock edge to sample on',
            'default': 'rising', 'values': ('rising', 'falling')},
        {'id': 'wordsize', 'desc': 'Data wordsize (# bus cycles)',
            'default': 0},
        {'id': 'endianness', 'desc': 'Data endianness',
            'default': 'little', 'values': ('little', 'big')},
    )
    annotations = (
        ('items', 'Items'),
        ('words', 'Words'),
    )
    annotation_rows = (
        ('items', 'Items', (0,)),
        ('words', 'Words', (1,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'FIND START'
        self.lcd_width = 264
        self.lcd_height = 64
        self.window = sdl2.ext.Window(
            "LCD Monochrome Display", size=(self.lcd_width, self.lcd_height))
        self.window.show()
        self.renderer = sdl2.ext.Renderer(self.window)
        self.items = []
        self.saved_item = None
        self.ss_item = self.es_item = None
        self.saved_word = None
        self.ss_word = self.es_word = None
        self.first = True
        self.ann = ["Start", "St", "S"]
        self.last_rw = 0
        self.last_cs1 = 0
        self.pages=[]
        [self.pages.append(i) for i in range(176,184)]

    def get_sample_length(self, num_samples):
        return 1/float(self.options['sample_rate'])*num_samples

    def get_time(self, samplenum):
        return round(1/float(self.options['sample_rate'])*1000*samplenum, 5)

    def start(self):
        self.out_python = self.register(srd.OUTPUT_PYTHON)
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putpb(self, data):
        self.put(self.ss_item, self.es_item, self.out_python, data)

    def putb(self, data):
        self.put(self.ss_item, self.es_item, self.out_ann, data)

    def putpw(self, data):
        self.put(self.ss_word, self.es_word, self.out_python, data)

    def putw(self, data):
        self.put(self.ss_word, self.es_word, self.out_ann, data)

    def handle_bits(self, item, used_pins):

        if self.first:
            self.ss_item = self.samplenum
            self.first = False
            self.saved_item = item
        else:
            self.es_item = self.samplenum
            self.putpb(['ITEM', self.saved_item])
            self.putb([0, self.ann])
            self.ss_item = self.samplenum
            self.saved_item = item

        # self.put(self.samplenum, 20, self.out_ann, [4, ['Start', 'St', 'S']])

    def updateLCD(self, cs1_device, command, bytes):
        page=self.pages.index(command[0])
        print("updateLCD", "CS1" if cs1_device else "CS2", command[0], len(bytes))
        print("Page", self.pages.index(command[0]))
        for i in range(len(bytes)):
            line="".join(reversed('{:08b}'.format(bytes[i])))
            mapping = [7,1,2,3,4,5,6,0]
            for y in range(8):
                try:
                    # y_mapped=mapping.index(int(y))
                    color = (0,0,0) if line[y] == '0' else (255,255,255)
                    self.renderer.draw_point([i+(0 if cs1_device else 132),y+(8*page)], sdl2.ext.Color(*color))
                    
                    
                except Exception as e:
                    print("somethings wrong", e)
        self.window.refresh()
        self.renderer.present()
        
    def decode(self):
        max_possible = len(self.optional_channels)
        idx_channels = [
            idx if self.has_channel(idx) else None
            for idx in range(max_possible)
        ]

        has_channels = [idx for idx in idx_channels if idx is not None]
        if not has_channels:
            raise ChannelError('At least one channel has to be supplied.')

        max_connected = max(has_channels)
        idx_strip = max_connected + 1
        num_item_bits = idx_strip - 1

        num_digits = (num_item_bits + 3) // 4
        self.fmt_item = "{{:0{}x}}".format(num_digits)

        previous_cs1_sample = 0
        previous_cs2_sample = 0
        cs1_count = 0
        cs2_count = 0

        cs1e = False
        prev_cs1 = 1
        prev_cs2 = 1
        prev_clk = 0
        prev_clk_cmd_count = 0
        prev_clk_sample = 0
        clk_cnt = 0
        start_clk_samplenum = 0
        command = []
        data_bytes = []
        while True:

            if self.state == 'FIND START':
                clk_cnt = 0
                command = []
                self.wait([{1: 'h', 0: 'f'}, {0: 'h', 1: 'f'}])
                print("FIND START", self.get_time(self.samplenum))
                potential_start = self.samplenum
                self.state = 'VERIFY START'
            elif (self.state == 'VERIFY START'):
                (cs1, cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7) = self.wait(
                    [{1: 'h', 0: 'l', 2: 'r'}, {0: 'h', 1: 'l', 2: 'r'}])

                print("VERIFY START", self.get_time(self.samplenum))
                current_time = self.get_time(self.samplenum)*1000
                potential_start_time = self.get_time(potential_start)*1000
                if(current_time-potential_start_time < 2.40):
                    command.append(bitpack((d0, d1, d2, d3, d4, d5, d6, d7)))
                    self.state = "FIND NEXT START CLK"
                    start_clk_samplenum = self.samplenum
                    clk_cnt += 1
                else:
                    self.state = 'FIND START'
            elif (self.state == 'FIND NEXT START CLK'):

                (cs1, cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7) = self.wait(
                    [{1: 'h', 0: 'l', 2: 'r'}, {0: 'h', 1: 'l', 2: 'r'}])
                print("FIND NEXT START CLK", self.get_time(self.samplenum))
                start_clk_time = self.get_time(start_clk_samplenum)*1000
                current_clk_time = self.get_time(self.samplenum)*1000
                # print("current_clk_time - start_clk_time", current_clk_time - start_clk_time)
                if(3.7 < (current_clk_time - start_clk_time) < 4.2):
                    start_clk_samplenum = self.samplenum
                    print("next start clk", self.get_time(self.samplenum))
                    print("cs1 cs2 clk", cs1, cs2, clk)
                    command.append(bitpack((d0, d1, d2, d3, d4, d5, d6, d7)))
                    clk_cnt += 1
                else:
                    self.state = 'FIND START'
                if(clk_cnt == 3):
                    print("clk 3", self.get_time(self.samplenum))
                    print("command", command)
                    self.state = 'READ DATA'
                    data_bytes = []
            elif (self.state == 'READ DATA'):
                (cs1, cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7) = self.wait(
                    [{1: 'h', 0: 'l', 2: 'r'}, {0: 'h', 1: 'l', 2: 'r'}])
                byte = bitpack((d0, d1, d2, d3, d4, d5, d6, d7))
                data_bytes.append(byte)

                if(len(data_bytes) == 132):
                    print("bytes", data_bytes, command)
                    cs1_device = cs2 and not cs1
                    self.updateLCD(cs1_device, command, data_bytes)
                    self.state = 'FIND START'

            # (cs1, cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7) = self.wait([{0:'h',1:'l'},{0:'l',1:'h'},{2:'r'}])
            # pins = (cs1, cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7)

            # # print(self.matched)
            # if self.matched == (False, True, True):
            #     print("cs1 clk", self.get_time(self.samplenum))

            # if self.matched == (True, False, True):
            #     print("cs2 clk", self.get_time(self.samplenum))

            # if(not cs2 and prev_cs2):
            #     cs2e=True
            #     print("edge cs2", self.get_time(self.samplenum))

            # else:
            #     cs2e=False
            # if(not cs1 and prev_cs1):
            #     cs1e=True
            #     print("edge cs1", self.get_time(self.samplenum))
            # else:
            #     cs1e=False
            # prev_cs1=cs1
            # prev_cs2=cs2
            # if(clk and not prev_clk):
            #     prev_clk_time = self.get_time(prev_clk_sample)*1000
            #     cur_clk_time = self.get_time(self.samplenum)*1000
            #     # print(cur_clk_time, prev_clk_time, cur_clk_time - prev_clk_time)
            #     if(prev_clk_sample==0 or ((cur_clk_time - prev_clk_time) > 3.8 and (cur_clk_time - prev_clk_time) < 4.5)):
            #         print("rising clk cmd", self.get_time(self.samplenum))
            #         prev_clk_cmd_count+=1
            #     prev_clk_sample= 0 if prev_clk_cmd_count==2 else self.samplenum
            # prev_clk=clk

            # previous_sample_time=self.get_time(previous_cs1_sample)
            # current_sample_time=self.get_time(self.samplenum)
            # cs_take_sample = previous_cs1_sample == 0 or (current_sample_time - previous_sample_time > 0.830 and current_sample_time - previous_sample_time < 0.84)
            # if(cs_take_sample):
            #     print("page",self.get_time(self.samplenum))
            #     while(cs1):
            #         self.wait({2:'r'})
            #         print("clk", self.get_time(self.samplenum))
            # previous_cs1_sample = self.samplenum

            # pins = (cs1,  cs2, clk, rw, e, d0, d1, d2, d3, d4, d5, d6, d7)
            # bits = [0 if idx is None else pins[idx] for idx in idx_channels]
            # item = bitpack(bits[1:idx_strip])
            # self.handle_bits(item, num_item_bits)
            # print(self.get_time(self.samplenum))
