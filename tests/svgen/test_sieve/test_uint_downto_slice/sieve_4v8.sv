module sieve_4v8
  (
   input clk,
   input rst,
   dti.consumer din,
   dti.producer dout

   );

   assign dout.data = {din.data[7:4]};

   assign dout.valid = din.valid;
   assign din.ready  = dout.ready;

endmodule